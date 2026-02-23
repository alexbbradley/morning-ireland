#!/usr/bin/env python3
"""
Morning Ireland RSS Analyser
-----------------------------
Run this script regularly (e.g. daily via cron) to accumulate data.
Each run fetches the RSS feed, deduplicates against what's already saved,
appends new items to a CSV, and regenerates the HTML visualisation.

Usage:
    python morning_ireland.py                  # fetch + save + visualise
    python morning_ireland.py --chart-only     # just regenerate chart from saved CSV
"""

import xml.etree.ElementTree as ET
import csv
import os
import sys
import json
import re
from datetime import datetime, date
from collections import defaultdict
import urllib.request
import argparse

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
RSS_URL   = "https://www.rte.ie/radio1/podcast/podcast_morningireland.xml"
CSV_FILE  = "morning_ireland_data.csv"
HTML_FILE = "morning_ireland_chart.html"

# Topic categories — keyword matching on segment title (case-insensitive)
# Checked in order; first match wins. "Other" is the fallback.
CATEGORIES = [
    ("Road Accidents",      ["road accident", "road crash", "road death", "road fatality", "road collision", "fatal crash", "fatal collision", "rsa "]),
    ("Sports",              ["sports news", "sport"]),
    ("Business",            ["business news"]),
    ("Weather",             ["weather forecast", "weather warning", "rainfall", "flooding", "flood", "orange alert", "yellow warning", "orange warning", "met éireann", "met eireann", "rain warning"]),
    ("News Bulletin",       ["news bulletin"]),
    ("It Says in Papers",   ["it says in the paper"]),
    ("Health / HSE",        ["hse", "hospital", "mental health", "camhs", "nursing", "psychiatr", "cancer", "cardiac", "heart attack", "surgery", "sna", "disability", "thalidomide", "tobacco", "smoking"]),
    ("Crime / Justice",     ["garda", "gardaí", "murder", "attack", "arson", "shooting", "theft", "prison", "court", "criminal", "crime", "regency", "stardust", "cloverhill"]),
    ("Politics / Gov",      ["minister", "taoiseach", "dáil", "oireachtas", "government", "coalition", "sinn féin", "fine gael", "fianna fáil", "labour", "green party", "council", "mayor", "senator", "td "]),
    ("Housing",             ["housing", "tenant", "hap ", "rent", "apartment", "home buying", "homeless", "accommodation", "fire safety"]),
    ("International",       ["trump", "gaza", "ukraine", "russia", "iran", "israel", "eu ", "european", "uk ", "britain", "brexit", "hong kong", "epstein", "maxwell", "canada", "sudan", "darfur", "nato", "munich security"]),
    ("Agriculture",         ["farm", "slurry", "crop", "livestock", "agri"]),
    ("Arts / Culture",      ["film", "music", "theatre", "book", "festival", "award", "michelin", "hot press", "orchestra", "poet", "director", "actor", "netflix", "cinema", "gallery", "exhibition"]),
    ("Science / Nature",    ["climate", "wildlife", "whale", "dolphin", "hedgehog", "toxic plant", "conservation", "epa", "environment", "ecology", "botany"]),
    ("Tech / Digital",      ["social media", "ai ", "artificial intelligence", "data protection", "grok", "internet", "cybersafe", "digital"]),
    ("Transport",           ["dublin airport", "airport", "bus", "rail", "irish rail", "flight", "donegal"]),
    ("Northern Ireland",    ["northern ireland", "uup", "dup", "sinn féin", "troubles", "stakeknife", "derry", "belfast", "stormont"]),
    ("Dublin",              ["city centre", "inner city", "docklands", "liberties", "phoenix park", "luas", "dart "]),
]
FALLBACK_CATEGORY = "Other"


# ─────────────────────────────────────────────
# PARSING
# ─────────────────────────────────────────────

def parse_duration(s):
    """Convert H:MM:SS or MM:SS to total seconds."""
    parts = [int(x) for x in s.strip().split(":")]
    if len(parts) == 3:
        return parts[0]*3600 + parts[1]*60 + parts[2]
    elif len(parts) == 2:
        return parts[0]*60 + parts[1]
    return 0

def categorise(title):
    tl = title.lower()
    for cat, keywords in CATEGORIES:
        if any(kw in tl for kw in keywords):
            return cat
    return FALLBACK_CATEGORY

def fetch_rss(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()

def parse_feed(xml_bytes):
    ns = {
        "itunes":  "http://www.itunes.com/dtds/podcast-1.0.dtd",
        "clipper": "http://www.rte.ie/applications/ipad/schemas",
    }
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.findall(".//item"):
        title_el    = item.find("title")
        desc_el     = item.find("description")
        dur_el      = item.find("itunes:duration", ns)
        date_el     = item.find("clipper:date", ns)
        guid_el     = item.find("guid")

        if not all([title_el is not None, dur_el is not None, date_el is not None]):
            continue

        title    = title_el.text.strip()
        desc     = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
        duration = parse_duration(dur_el.text)
        seg_date = date_el.text.strip()          # yyyy-mm-dd
        guid     = guid_el.text.strip() if guid_el is not None else f"{seg_date}-{title}"
        category = categorise(title)

        items.append({
            "guid":     guid,
            "date":     seg_date,
            "title":    title,
            "desc":     desc,
            "duration": duration,
            "category": category,
        })
    return items


# ─────────────────────────────────────────────
# CSV STORAGE
# ─────────────────────────────────────────────
FIELDNAMES = ["guid", "date", "title", "desc", "duration", "category"]

def load_existing_guids(path):
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {row["guid"] for row in csv.DictReader(f)}

def append_new_items(path, items, existing_guids):
    new_items = [i for i in items if i["guid"] not in existing_guids]
    if not new_items:
        return 0
    write_header = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(new_items)
    return len(new_items)

def load_all_items(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["duration"] = int(r["duration"])
    return rows


# ─────────────────────────────────────────────
# VISUALISATION  (self-contained HTML + Plotly CDN)
# ─────────────────────────────────────────────

# Colour palette for categories
PALETTE = {
    "Sports":             "#2196F3",
    "Business":           "#009688",
    "Weather":            "#78909C",
    "News Bulletin":      "#455A64",
    "It Says in Papers":  "#90A4AE",
    "Health / HSE":       "#E91E63",
    "Crime / Justice":    "#F44336",
    "Politics / Gov":     "#9C27B0",
    "Housing":            "#FF9800",
    "International":      "#F06292",
    "Agriculture":        "#8BC34A",
    "Arts / Culture":     "#FFEB3B",
    "Science / Nature":   "#4CAF50",
    "Tech / Digital":     "#00BCD4",
    "Transport":          "#FF5722",
    "Northern Ireland":   "#7B1FA2",
    "Road Accidents":     "#795548",
    "Dublin":             "#283593",
    "Other":              "#BDBDBD",
}

def build_chart(items, out_path):
    # Aggregate: for each (date, category) sum minutes
    agg = defaultdict(lambda: defaultdict(float))
    for item in items:
        agg[item["date"]][item["category"]] += item["duration"] / 60.0

    dates = sorted(agg.keys())
    all_cats = [c for c, _ in CATEGORIES] + [FALLBACK_CATEGORY]
    # Only include categories that actually appear in the data
    present_cats = [c for c in all_cats if any(agg[d].get(c, 0) > 0 for d in dates)]

    # Build one trace per category
    traces = []
    for cat in present_cats:
        y_vals = [round(agg[d].get(cat, 0), 1) for d in dates]
        # Tooltip: show individual segments for that date+cat
        hover = []
        for d in dates:
            segs = [i for i in items if i["date"] == d and i["category"] == cat]
            if segs:
                lines = [f"<b>{cat} — {d}</b>"]
                for s in sorted(segs, key=lambda x: x["duration"], reverse=True):
                    mins = s["duration"] // 60
                    secs = s["duration"] % 60
                    lines.append(f"• {s['title']} ({mins}m {secs:02d}s)")
                hover.append("<br>".join(lines))
            else:
                hover.append("")

        traces.append({
            "type":        "bar",
            "name":        cat,
            "x":           dates,
            "y":           y_vals,
            "text":        hover,
            "hovertemplate": "%{text}<extra></extra>",
            "marker":      {"color": PALETTE.get(cat, "#BDBDBD")},
        })

    layout = {
        "title":    {"text": "Morning Ireland — Time Spent by Topic per Day", "font": {"size": 20}},
        "barmode":  "stack",
        "xaxis":    {"title": "Date", "tickangle": -45, "type": "category"},
        "yaxis":    {"title": "Minutes"},
        "legend":   {"orientation": "h", "y": -0.25},
        "height":   600,
        "plot_bgcolor": "#fafafa",
        "paper_bgcolor": "#ffffff",
    }

    # Produce self-contained HTML
    fig_json = json.dumps({"data": traces, "layout": layout})
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Morning Ireland Topic Analysis</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/plotly.js/2.26.0/plotly.min.js"></script>
<style>
  body {{ font-family: sans-serif; margin: 20px; background: #fff; color: #222; }}
  h1   {{ font-size: 1.2em; color: #555; }}
  #info {{ font-size: 0.85em; color: #888; margin-bottom: 10px; }}
</style>
</head>
<body>
<h1>Morning Ireland — Topic Analysis</h1>
<div id="info">
  {len(items)} segments across {len(dates)} days
  &nbsp;|&nbsp; Last updated: {datetime.now().strftime("%d %b %Y %H:%M")}
  &nbsp;|&nbsp; Hover bars for segment detail
</div>
<div id="chart"></div>
<script>
  var fig = {fig_json};
  Plotly.newPlot('chart', fig.data, fig.layout, {{responsive: true, displayModeBar: true}});
</script>
</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return len(dates)


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chart-only", action="store_true",
                        help="Skip RSS fetch; regenerate chart from saved CSV only")
    args = parser.parse_args()

    if not args.chart_only:
        print(f"Fetching {RSS_URL} …")
        try:
            xml_bytes = fetch_rss(RSS_URL)
        except Exception as e:
            print(f"  ERROR fetching feed: {e}")
            sys.exit(1)

        items = parse_feed(xml_bytes)
        print(f"  Parsed {len(items)} segments from feed")

        existing = load_existing_guids(CSV_FILE)
        added = append_new_items(CSV_FILE, items, existing)
        print(f"  {added} new segments appended to {CSV_FILE}  "
              f"({len(existing)} already stored)")
    else:
        print("Skipping fetch — using existing CSV")

    all_items = load_all_items(CSV_FILE)
    if not all_items:
        print("No data in CSV yet. Run without --chart-only first.")
        sys.exit(0)

    n_days = build_chart(all_items, HTML_FILE)
    print(f"  Chart written to {HTML_FILE}  ({len(all_items)} segments, {n_days} days)")
    print()
    print("Open morning_ireland_chart.html in a browser to view.")
    print()
    print("To build history, add this to your crontab:")
    print("  0 10 * * 1-5  cd /path/to/script && python morning_ireland.py >> morning_ireland.log 2>&1")

if __name__ == "__main__":
    main()
