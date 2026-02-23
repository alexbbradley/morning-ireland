#!/usr/bin/env node
/**
 * Morning Ireland — HTML build script
 *
 * Reads ../transcripts/YYYY-MM-DD.json files, aggregates time per category
 * per day, compiles Tailwind CSS, injects everything into src/template.html,
 * and writes a self-contained docs/index.html.
 *
 * Usage:
 *   node src/build.js           # build once
 *   node src/build.js --watch   # rebuild whenever a transcript changes
 *
 * Normally invoked via npm scripts (see package.json):
 *   npm run build
 *   npm run watch
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// ── Paths ─────────────────────────────────────────────────────────────────────

const ROOT            = path.resolve(__dirname, '..');
const TRANSCRIPTS_DIR = path.join(ROOT, 'transcripts');
const TMPL            = path.join(__dirname, 'template.html');
const IN_CSS          = path.join(__dirname, 'styles.css');
const DIST            = path.join(ROOT, 'docs');
const OUT_CSS         = path.join(DIST, 'styles.css');
const OUT_HTML        = path.join(DIST, 'index.html');

// Resolve tailwindcss binary installed locally (works on Windows and Unix)
const TW_EXT = process.platform === 'win32' ? '.cmd' : '';
const TW_BIN = path.join(ROOT, 'node_modules', '.bin', `tailwindcss${TW_EXT}`);

// ── Category order + colour palette ───────────────────────────────────────────

const CATEGORY_ORDER = [
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
  'Ads',
  'Other',
];

const PALETTE = {
  'Sports':            '#2196F3',
  'Business':          '#009688',
  'Weather':           '#78909C',
  'News Bulletin':     '#455A64',
  'It Says in Papers': '#90A4AE',
  'Health / HSE':      '#E91E63',
  'Crime / Justice':   '#F44336',
  'Politics / Gov':    '#9C27B0',
  'Housing':           '#FF9800',
  'International':     '#F06292',
  'Agriculture':       '#8BC34A',
  'Arts / Culture':    '#FFEB3B',
  'Science / Nature':  '#4CAF50',
  'Tech / Digital':    '#00BCD4',
  'Transport':         '#FF5722',
  'Northern Ireland':  '#7B1FA2',
  'Road Accidents':    '#795548',
  'Dublin':            '#283593',
  'Ads':               '#E0E0E0',
  'Other':             '#BDBDBD',
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDuration(secs) {
  return `${Math.floor(secs / 60)}m ${String(secs % 60).padStart(2, '0')}s`;
}

function buildTimestamp() {
  return new Date().toLocaleString('en-IE', { dateStyle: 'medium', timeStyle: 'short' });
}

// ── Core build ────────────────────────────────────────────────────────────────

function build() {
  const t0 = Date.now();
  process.stdout.write('Building… ');

  // 1. Read all transcript JSON files
  const files = fs.readdirSync(TRANSCRIPTS_DIR)
    .filter(f => /^\d{4}-\d{2}-\d{2}\.json$/.test(f))
    .sort();

  if (files.length === 0) {
    console.error('No transcript JSON files found in transcripts/');
    process.exit(1);
  }

  // 2. Aggregate: agg[date][category] = { mins, segs[] }
  const agg = {};
  let totalSegs = 0;

  for (const file of files) {
    const date = file.replace('.json', '');
    const data = JSON.parse(fs.readFileSync(path.join(TRANSCRIPTS_DIR, file), 'utf8'));

    agg[date] = {};
    for (const seg of data.segments) {
      const cat     = seg.category || 'Other';
      const durSecs = Math.max(0, seg.end_s - seg.start_s);

      if (!agg[date][cat]) agg[date][cat] = { mins: 0, segs: [] };
      agg[date][cat].mins += durSecs / 60;
      agg[date][cat].segs.push({ title: seg.title, duration: durSecs, start_s: seg.start_s });
    }
    totalSegs += data.segments.length;
  }

  const dates = Object.keys(agg).sort();

  // Collect all categories that appear in the data, ordered by CATEGORY_ORDER
  const seenCats = new Set(dates.flatMap(d => Object.keys(agg[d])));
  const presentCats = [
    ...CATEGORY_ORDER.filter(c => seenCats.has(c)),
    ...[...seenCats].filter(c => !CATEGORY_ORDER.includes(c)).sort(),
  ];

  // 3. Build Plotly traces
  const traces = presentCats.map(cat => {
    const y = dates.map(d => {
      const m = agg[d][cat]?.mins ?? 0;
      return Math.round(m * 10) / 10;
    });
    const text = dates.map(d => {
      const bucket = agg[d][cat];
      if (!bucket) return '';
      const sorted = [...bucket.segs].sort((a, b) => b.duration - a.duration);
      return [
        `<b>${cat} \u2014 ${d}</b>`,
        ...sorted.map(s => `\u2022 ${s.title} (${fmtDuration(s.duration)})`),
      ].join('<br>');
    });
    return {
      type: 'bar',
      name: cat,
      x: dates,
      y,
      text,
      hovertemplate: '%{text}<extra></extra>',
      marker: { color: PALETTE[cat] ?? '#BDBDBD' },
    };
  });

  const layout = {
    title: { text: 'Morning Ireland \u2014 Time Spent by Topic per Day', font: { size: 20 } },
    barmode: 'stack',
    xaxis: { title: 'Date', tickangle: -45, type: 'category' },
    yaxis: { title: 'Minutes' },
    legend: { orientation: 'h', y: -0.25 },
    plot_bgcolor: '#fafafa',
    paper_bgcolor: '#ffffff',
  };

  // 4. Compile Tailwind CSS
  fs.mkdirSync(DIST, { recursive: true });
  try {
    execSync(`"${TW_BIN}" -i "${IN_CSS}" -o "${OUT_CSS}" --minify`, {
      cwd: ROOT,
      stdio: 'pipe',
    });
  } catch (err) {
    const msg = err.stderr?.toString().trim();
    if (msg) console.warn('\nTailwind warning:', msg);
  }
  const css = fs.readFileSync(OUT_CSS, 'utf8');

  // 5. Render template — replace all {{TOKEN}} placeholders
  let html = fs.readFileSync(TMPL, 'utf8');
  html = html
    .replace('{{STYLES}}',        css)
    .replace('{{CHART_DATA}}',    JSON.stringify({ data: traces, layout }))
    .replace('{{SEGMENT_COUNT}}', totalSegs)
    .replace('{{DAY_COUNT}}',     dates.length)
    .replace('{{BUILD_TIME}}',    buildTimestamp());

  fs.writeFileSync(OUT_HTML, html, 'utf8');

  // Copy CNAME into dist/ if present (GitHub Pages custom domain)
  const cnameSrc = path.join(ROOT, 'CNAME');
  if (fs.existsSync(cnameSrc)) {
    fs.copyFileSync(cnameSrc, path.join(DIST, 'CNAME'));
  }

  console.log(`done (${totalSegs} segs, ${dates.length} days, ${Date.now() - t0}ms)`);
  console.log(`  → ${OUT_HTML}`);
}

// ── Entry ─────────────────────────────────────────────────────────────────────

try {
  build();
} catch (err) {
  console.error('Build failed:', err.message);
  process.exit(1);
}

if (process.argv.includes('--watch')) {
  console.log(`\nWatching ${TRANSCRIPTS_DIR} for changes…`);
  let debounce;
  fs.watch(TRANSCRIPTS_DIR, () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      try { build(); } catch (err) { console.error('Rebuild failed:', err.message); }
    }, 300);
  });
}
