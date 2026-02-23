#!/usr/bin/env node
/**
 * Morning Ireland — HTML build script
 *
 * Reads ../morning_ireland_data.csv, aggregates data by date + category,
 * compiles Tailwind CSS, injects everything into src/template.html, and
 * writes a self-contained dist/index.html.
 *
 * Usage:
 *   node src/build.js           # build once
 *   node src/build.js --watch   # rebuild whenever the CSV changes
 *
 * Normally invoked via npm scripts (see package.json):
 *   npm run build
 *   npm run watch
 *   npm run update   # python fetch → node build
 */

'use strict';

const fs   = require('fs');
const path = require('path');
const { execSync }  = require('child_process');
const { parse }     = require('csv-parse/sync');

// ── Paths ─────────────────────────────────────────────────────────────────────

const ROOT     = path.resolve(__dirname, '..');
const CSV_FILE = path.join(ROOT, 'morning_ireland_data.csv');
const TMPL     = path.join(__dirname, 'template.html');
const IN_CSS   = path.join(__dirname, 'styles.css');
const DIST     = path.join(ROOT, 'dist');
const OUT_CSS  = path.join(DIST, 'styles.css');
const OUT_HTML = path.join(DIST, 'index.html');

// Resolve tailwindcss binary installed locally (works on Windows and Unix)
const TW_EXT = process.platform === 'win32' ? '.cmd' : '';
const TW_BIN = path.join(ROOT, 'node_modules', '.bin', `tailwindcss${TW_EXT}`);

// ── Category config (mirrors morning_ireland.py) ──────────────────────────────

const CATEGORIES = [
  ['Sports',            ['sports news', 'sport']],
  ['Business',          ['business news']],
  ['Weather',           ['weather forecast', 'weather warning', 'rainfall', 'flooding', 'flood', 'orange alert', 'yellow warning', 'orange warning', 'met éireann', 'met eireann', 'rain warning']],
  ['News Bulletin',     ['news bulletin']],
  ['It Says in Papers', ['it says in the paper']],
  ['Health / HSE',      ['hse', 'hospital', 'mental health', 'camhs', 'nursing', 'psychiatr', 'cancer', 'cardiac', 'heart attack', 'surgery', 'sna', 'disability', 'thalidomide', 'tobacco', 'smoking']],
  ['Crime / Justice',   ['garda', 'gardaí', 'murder', 'attack', 'arson', 'shooting', 'theft', 'prison', 'court', 'criminal', 'crime', 'regency', 'stardust', 'cloverhill']],
  ['Politics / Gov',    ['minister', 'taoiseach', 'dáil', 'oireachtas', 'government', 'coalition', 'sinn féin', 'fine gael', 'fianna fáil', 'labour', 'green party', 'council', 'mayor', 'senator', 'td ']],
  ['Housing',           ['housing', 'tenant', 'hap ', 'rent', 'apartment', 'home buying', 'homeless', 'accommodation', 'fire safety']],
  ['International',     ['trump', 'gaza', 'ukraine', 'russia', 'iran', 'israel', 'eu ', 'european', 'uk ', 'britain', 'brexit', 'hong kong', 'epstein', 'maxwell', 'canada', 'sudan', 'darfur', 'nato', 'munich security']],
  ['Agriculture',       ['farm', 'slurry', 'crop', 'livestock', 'agri']],
  ['Arts / Culture',    ['film', 'music', 'theatre', 'book', 'festival', 'award', 'michelin', 'hot press', 'orchestra', 'poet', 'director', 'actor', 'netflix', 'cinema', 'gallery', 'exhibition']],
  ['Science / Nature',  ['climate', 'wildlife', 'whale', 'dolphin', 'hedgehog', 'toxic plant', 'conservation', 'epa', 'environment', 'ecology', 'botany']],
  ['Tech / Digital',    ['social media', 'ai ', 'artificial intelligence', 'data protection', 'grok', 'internet', 'cybersafe', 'digital']],
  ['Transport',         ['dublin airport', 'airport', 'bus', 'rail', 'irish rail', 'flight', 'donegal']],
  ['Northern Ireland',  ['northern ireland', 'uup', 'dup', 'sinn féin', 'troubles', 'stakeknife', 'derry', 'belfast', 'stormont']],
  ['Road Accidents',    ['road accident', 'road crash', 'road death', 'road fatality', 'road collision', 'fatal crash', 'fatal collision', 'rsa ']],
  ['Dublin',            ['city centre', 'inner city', 'docklands', 'liberties', 'phoenix park', 'luas', 'dart ']],
];

const FALLBACK = 'Other';

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
  'Other':             '#BDBDBD',
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function categorise(title) {
  const tl = title.toLowerCase();
  for (const [cat, kws] of CATEGORIES) {
    if (kws.some(kw => tl.includes(kw))) return cat;
  }
  return FALLBACK;
}

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

  // 1. Parse CSV
  const csvText = fs.readFileSync(CSV_FILE, 'utf8');
  const rows = parse(csvText, { columns: true, skip_empty_lines: true });
  rows.forEach(r => { r.duration = parseInt(r.duration, 10); });

  // 2. Aggregate: agg[date][category] = { mins, segs[] }
  const agg = {};
  for (const r of rows) {
    if (!agg[r.date]) agg[r.date] = {};
    if (!agg[r.date][r.category]) agg[r.date][r.category] = { mins: 0, segs: [] };
    agg[r.date][r.category].mins += r.duration / 60;
    agg[r.date][r.category].segs.push(r);
  }

  const dates = Object.keys(agg).sort();
  const allCats = [...CATEGORIES.map(c => c[0]), FALLBACK];
  const presentCats = allCats.filter(c => dates.some(d => agg[d][c]?.mins > 0));

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
    .replace('{{STYLES}}',         css)
    .replace('{{CHART_DATA}}',     JSON.stringify({ data: traces, layout }))
    .replace('{{SEGMENT_COUNT}}',  rows.length)
    .replace('{{DAY_COUNT}}',      dates.length)
    .replace('{{BUILD_TIME}}',     buildTimestamp());

  fs.writeFileSync(OUT_HTML, html, 'utf8');

  // Copy CNAME into dist/ if present (GitHub Pages custom domain)
  const cnameSrc = path.join(ROOT, 'CNAME');
  if (fs.existsSync(cnameSrc)) {
    fs.copyFileSync(cnameSrc, path.join(DIST, 'CNAME'));
  }

  console.log(`done (${rows.length} segs, ${dates.length} days, ${Date.now() - t0}ms)`);
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
  console.log(`\nWatching ${CSV_FILE} for changes…`);
  let debounce;
  fs.watch(CSV_FILE, () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      try { build(); } catch (err) { console.error('Rebuild failed:', err.message); }
    }, 300);
  });
}
