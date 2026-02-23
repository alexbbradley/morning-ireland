#!/usr/bin/env python3
"""
Morning Ireland — Full Show Audio Transcription
------------------------------------------------
Downloads today's full 2-hour show via the RTÉ Player API,
transcribes it in chunks with OpenAI Whisper, uses Claude to identify
and categorise every news segment, and saves a JSON transcript to
transcripts/YYYY-MM-DD.json.

Usage:
    python morning_ireland_audio.py               # process today
    python morning_ireland_audio.py --date 2026-02-21
    python morning_ireland_audio.py --force       # re-process even if file exists
"""

import os
import sys
import re
import json
import subprocess
import tempfile
import argparse
import urllib.request
from datetime import date

try:
    import openai as _openai
except ImportError:
    _openai = None

try:
    import anthropic as _anthropic
except ImportError:
    _anthropic = None

# ── Category list (mirrors morning_ireland.py) ────────────────────────────────

CATEGORY_NAMES = [
    'Road Accidents',
    'Sports',
    'Business',
    'Weather',
    'News Bulletin',
    'It Says in Papers',
    'Health / HSE',
    'Crime / Justice',
    'Politics / Gov',
    'Housing',
    'International',
    'Agriculture',
    'Arts / Culture',
    'Science / Nature',
    'Tech / Digital',
    'Transport',
    'Northern Ireland',
    'Dublin',
    'Other',
]

TRANSCRIPTS_DIR = os.path.join(os.path.dirname(__file__), 'transcripts')

# ── RTÉ episode discovery ─────────────────────────────────────────────────────

def get_episode_slug_for_date(target_date):
    """
    Scrape the Morning Ireland listing page and return the episode slug
    (e.g. '/radio/radio1/morning-ireland/2026/0223/1559915-...')
    for the given date string 'YYYY-MM-DD'.  Returns None if not found.
    """
    url = 'https://www.rte.ie/radio/radio1/morning-ireland/'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode('utf-8')

    slugs = re.findall(
        r'(/radio/radio1/morning-ireland/\d{4}/\d{4}/\d+-[^"\'<>\s]+)', html
    )
    seen, unique = set(), []
    for s in slugs:
        s = s.rstrip('/')
        if s not in seen:
            seen.add(s)
            unique.append(s)

    # e.g. target_date='2026-02-23' → '2026/0223'
    date_path = target_date[:4] + '/' + target_date[5:7] + target_date[8:10]
    for slug in unique:
        if date_path in slug:
            return slug
    return None


def get_clipper_id(slug):
    """Fetch the episode page and extract the RTÉ clipper ID."""
    url = 'https://www.rte.ie' + slug + '/'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15) as r:
        html = r.read().decode('utf-8')
    ids = re.findall(r'clipper:(\d+)', html)
    if not ids:
        raise ValueError(f'No clipper ID found on {url}')
    return ids[0]


def get_hls_url(clipper_id):
    """
    Call the RTÉ playlist API and return (hls_url, duration_s).
    hls_url is the full https://cdn.rasset.ie/… URL.
    """
    api = (
        f'https://www.rte.ie/rteavgen/getplaylist/'
        f'?type=web&format=json&id={clipper_id}'
    )
    req = urllib.request.Request(api, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())
    show  = data['shows'][0]
    media = show['media:group'][0]
    hls   = media['hls_server'] + media['hls_url']
    dur_s = media['duration'] // 1000
    return hls, dur_s


# ── Audio processing ──────────────────────────────────────────────────────────

def download_audio(hls_url, output_path):
    """Download HLS stream to MP3 via yt-dlp (handles RTÉ CDN headers/auth)."""
    print(f'  Downloading {hls_url} …', flush=True)
    subprocess.run(
        [
            sys.executable, '-m', 'yt_dlp',
            '--no-warnings', '--quiet',
            '-x', '--audio-format', 'mp3', '--audio-quality', '5',
            '--add-header', 'Referer:https://www.rte.ie/',
            '-o', output_path,
            hls_url,
        ],
        check=True,
    )
    mb = os.path.getsize(output_path) / 1024 / 1024
    print(f'  Downloaded {mb:.1f} MB')


def split_audio(input_path, output_dir, chunk_secs=600):
    """Split MP3 into chunk_secs-second segments; return sorted list of paths."""
    pattern = os.path.join(output_dir, 'chunk_%03d.mp3')
    subprocess.run(
        [
            'ffmpeg', '-y',
            '-i', input_path,
            '-f', 'segment', '-segment_time', str(chunk_secs),
            '-c', 'copy', '-loglevel', 'error',
            pattern,
        ],
        check=True,
    )
    return sorted(
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.startswith('chunk_') and f.endswith('.mp3')
    )


def transcribe_chunks(chunks, oa_client):
    """
    Transcribe each chunk with Whisper (verbose_json, sentence-level timestamps).
    Offsets each chunk's timestamps and returns a combined list of segments:
      [{'start': float, 'end': float, 'text': str}, …]
    """
    all_segs = []
    offset   = 0.0

    for i, chunk_path in enumerate(chunks):
        print(f'  Chunk {i+1}/{len(chunks)} … ', end='', flush=True)
        with open(chunk_path, 'rb') as f:
            result = oa_client.audio.transcriptions.create(
                model='whisper-1',
                file=f,
                response_format='verbose_json',
            )
        for seg in result.segments:
            all_segs.append({
                'start': round(seg.start + offset, 1),
                'end':   round(seg.end   + offset, 1),
                'text':  seg.text.strip(),
            })
        if result.segments:
            offset += result.segments[-1].end
        print(f'{len(result.segments)} phrases (offset {offset/60:.1f}m)')

    return all_segs


# ── Claude segmentation ───────────────────────────────────────────────────────

def segment_with_claude(whisper_segs, claude_client):
    """
    Send the timestamped Whisper transcript to Claude and ask it to identify
    every distinct news segment with title, category, and summary.

    Returns a list of dicts:
      {'start_s': int, 'end_s': int, 'title': str, 'category': str, 'summary': str}
    """
    # Build a readable transcript with [MM:SS] markers every 30 seconds
    lines, last_marker = [], -30
    for seg in whisper_segs:
        if seg['start'] - last_marker >= 30:
            m, s = divmod(int(seg['start']), 60)
            lines.append(f'\n[{m:02d}:{s:02d}]')
            last_marker = seg['start']
        lines.append(seg['text'])
    transcript = ' '.join(lines)

    category_list = '\n'.join(f'- {c}' for c in CATEGORY_NAMES)

    prompt = (
        'This is a transcript of Morning Ireland, RTÉ Radio 1\'s flagship '
        'news programme (Mon–Fri, 7am–9am Irish time). Timestamp markers '
        'appear as [MM:SS].\n\n'
        f'Available categories:\n{category_list}\n\n'
        f'Transcript:\n{transcript}\n\n'
        'Identify every distinct news segment or item in this broadcast. '
        'For each one output:\n'
        '- start_s  (start time in seconds, integer)\n'
        '- end_s    (end time in seconds, integer)\n'
        '- title    (concise headline-style title)\n'
        '- category (exactly one category from the list above)\n'
        '- summary  (1–2 sentence summary)\n\n'
        'Output ONLY a valid JSON array, no markdown fences, no other text.'
    )

    print('  Sending to Claude for segmentation … ', end='', flush=True)
    msg = claude_client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=4096,
        messages=[{'role': 'user', 'content': prompt}],
    )
    text = msg.content[0].text.strip()
    # Strip any accidental code fences
    text = re.sub(r'^```(?:json)?\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    segments = json.loads(text)
    print(f'{len(segments)} segments')
    return segments


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Transcribe the full Morning Ireland show.')
    parser.add_argument('--date',  default=date.today().isoformat(),
                        help='Date to process (YYYY-MM-DD), default: today')
    parser.add_argument('--force', action='store_true',
                        help='Re-process even if transcript already exists')
    args = parser.parse_args()

    target_date     = args.date
    transcript_path = os.path.join(TRANSCRIPTS_DIR, f'{target_date}.json')

    if os.path.exists(transcript_path) and not args.force:
        print(f'Transcript already exists: {transcript_path}  (use --force to redo)')
        return

    # Check dependencies
    if _openai is None:
        print('ERROR: openai package not installed.  Run: pip install openai')
        sys.exit(1)
    if _anthropic is None:
        print('ERROR: anthropic package not installed.  Run: pip install anthropic')
        sys.exit(1)

    openai_key = os.environ.get('OPENAI_API_KEY')
    if not openai_key:
        print('ERROR: OPENAI_API_KEY environment variable not set')
        sys.exit(1)

    anthropic_key = os.environ.get('ANTHROPIC_API_KEY')
    if not anthropic_key:
        print('ERROR: ANTHROPIC_API_KEY environment variable not set')
        sys.exit(1)

    oa_client     = _openai.OpenAI(api_key=openai_key)
    claude_client = _anthropic.Anthropic(api_key=anthropic_key)

    # 1. Find episode
    print(f'Looking up episode for {target_date} …')
    slug = get_episode_slug_for_date(target_date)
    if not slug:
        print(f'  No episode found for {target_date} on the listing page.')
        sys.exit(0)
    print(f'  Slug: {slug}')

    # 2. Get HLS URL
    clipper_id = get_clipper_id(slug)
    hls_url, duration_s = get_hls_url(clipper_id)
    print(f'  Clipper ID : {clipper_id}')
    print(f'  Duration   : {duration_s//60}m {duration_s%60:02d}s')
    print(f'  HLS URL    : {hls_url}')

    # 3. Download + split + transcribe in a temp dir
    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, 'show.mp3')

        print('\nDownloading audio …')
        download_audio(hls_url, audio_path)

        print('\nSplitting into 10-minute chunks …')
        chunks = split_audio(audio_path, tmpdir)
        print(f'  {len(chunks)} chunks')

        print('\nTranscribing with Whisper …')
        whisper_segs = transcribe_chunks(chunks, oa_client)
        print(f'  Total phrases: {len(whisper_segs)}')

    # 4. Segment with Claude
    print('\nSegmenting with Claude …')
    segments = segment_with_claude(whisper_segs, claude_client)

    # 5. Save transcript
    transcript = {
        'date':       target_date,
        'clipper_id': int(clipper_id),
        'slug':       slug,
        'duration_s': duration_s,
        'segments':   segments,
    }
    with open(transcript_path, 'w', encoding='utf-8') as f:
        json.dump(transcript, f, indent=2, ensure_ascii=False)

    print(f'\nSaved → {transcript_path}')
    print(f'\nSegments ({len(segments)} total):')
    for seg in segments:
        m_s, m_e = seg['start_s'] // 60, seg['end_s'] // 60
        print(f"  [{m_s:02d}m–{m_e:02d}m]  {seg['category']:<20}  {seg['title']}")


if __name__ == '__main__':
    main()
