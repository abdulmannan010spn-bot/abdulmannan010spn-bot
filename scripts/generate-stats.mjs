// Generates assets/streak.svg from live GitHub contribution data.
// Usage: GH_TOKEN=... USERNAME=... node scripts/generate-stats.mjs   (add --mock to test offline)
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const G = JSON.parse(fs.readFileSync(path.join(here, 'glyphs.json'), 'utf8'));
const OUT = path.join(here, '..', 'assets', 'streak.svg');
const MOCK = process.argv.includes('--mock');
const USER = process.env.USERNAME || 'abdulmannan010spn-bot';
const TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;

const iso = (d) => d.toISOString().slice(0, 10);
const addDays = (s, n) => { const d = new Date(s + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return iso(d); };
const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const fmt = (s, year = false) => { const d = new Date(s + 'T00:00:00Z'); return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}${year ? ', ' + d.getUTCFullYear() : ''}`; };

// ---------- data ----------
async function gql(query, variables) {
  const r = await fetch('https://api.github.com/graphql', {
    method: 'POST',
    headers: { Authorization: `bearer ${TOKEN}`, 'Content-Type': 'application/json', 'User-Agent': 'streak-card' },
    body: JSON.stringify({ query, variables }),
  });
  const j = await r.json();
  if (!r.ok || j.errors) throw new Error(JSON.stringify(j.errors || j));
  return j.data;
}

async function loadDays() {
  const days = new Map();
  let lastYear = null;
  if (MOCK) {
    let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
    const end = iso(new Date());
    for (let d = addDays(end, -420); d <= end; d = addDays(d, 1)) days.set(d, rnd() < 0.35 ? 0 : Math.floor(rnd() * 9));
    days.set(end, 3);
    lastYear = [...days.entries()].filter(([d]) => d > addDays(end, -365)).reduce((a, [, c]) => a + c, 0);
    return { days, lastYear };
  }
  const q = `query($login:String!,$from:DateTime!,$to:DateTime!){user(login:$login){createdAt
    contributionsCollection(from:$from,to:$to){contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}`;
  const first = await gql(`query($l:String!){user(login:$l){createdAt}}`, { l: USER });
  const startYear = new Date(first.user.createdAt).getUTCFullYear();
  // exactly what github.com shows as "N contributions in the last year"
  const ly = await gql(`query($l:String!){user(login:$l){contributionsCollection{contributionCalendar{totalContributions}}}}`, { l: USER });
  lastYear = ly.user.contributionsCollection.contributionCalendar.totalContributions;
  const now = new Date();
  for (let y = startYear; y <= now.getUTCFullYear(); y++) {
    const from = `${y}-01-01T00:00:00Z`;
    const to = y === now.getUTCFullYear() ? now.toISOString() : `${y}-12-31T23:59:59Z`;
    const d = await gql(q, { login: USER, from, to });
    for (const w of d.user.contributionsCollection.contributionCalendar.weeks)
      for (const day of w.contributionDays) days.set(day.date, day.contributionCount);
  }
  return { days, lastYear };
}

function stats({ days, lastYear }) {
  const dates = [...days.keys()].sort();
  const today = dates[dates.length - 1];
  let total = 0; for (const c of days.values()) total += c;
  // longest
  let best = { len: 0, start: null, end: null }, run = 0, rs = null;
  for (const d of dates) {
    if (days.get(d) > 0) { if (!run) rs = d; run++; if (run > best.len) best = { len: run, start: rs, end: d }; } else run = 0;
  }
  // current (today may still be empty)
  let end = days.get(today) > 0 ? today : addDays(today, -1);
  let cur = { len: 0, start: null, end: null };
  for (let d = end; days.get(d) > 0; d = addDays(d, -1)) { cur.len++; cur.start = d; cur.end = end; }
  const first = dates.find((d) => days.get(d) > 0) || dates[0];
  const last30 = Array.from({ length: 30 }, (_, i) => days.get(addDays(today, i - 29)) ?? 0);
  return { total, lastYear, first, today, cur, best, last30 };
}

// ---------- drawing (outlined glyphs, so fonts never depend on the viewer) ----------
function width(str, font, size, ls = 0) {
  const f = G[font]; let w = 0;
  for (const ch of str) w += (f.chars[ch]?.[0] ?? f.chars[' '][0]) * size / f.upem + ls;
  return w;
}
function text(str, x, y, size, font, fill, { anchor = 'start', ls = 0 } = {}) {
  const f = G[font]; const w = width(str, font, size, ls);
  let cx = anchor === 'end' ? x - (w - ls) : anchor === 'middle' ? x - (w - ls) / 2 : x;
  const s = size / f.upem; let out = '';
  for (const ch of str) {
    const g = f.chars[ch] || f.chars[' '];
    if (g[1]) out += `<path transform="translate(${cx.toFixed(2)} ${y}) scale(${s.toFixed(5)} ${(-s).toFixed(5)})" d="${g[1]}"/>`;
    cx += g[0] * s + ls;
  }
  return `<g fill="${fill}">${out}</g>`;
}
const INK = '#0B0F1A', BLUE = '#2B4BFF', RED = '#F03A2E', YEL = '#FFC61A';

function render(s) {
  const W = 800, H = 250, CH = 232;
  const cols = [
    { x: 40, label: 'CONTRIBUTIONS · LAST YEAR', num: s.placeholder ? '—' : s.lastYear.toLocaleString('en-US'), color: BLUE, sub: s.placeholder ? 'Syncing on first run' : `${fmt(addDays(s.today, -364), true)} – Present` },
    { x: 294, label: 'CURRENT STREAK', num: String(s.cur.len), color: RED, sub: s.placeholder ? 'Syncing on first run' : s.cur.len ? `${fmt(s.cur.start)} – ${fmt(s.cur.end)}` : 'Make a commit today' , unit: s.placeholder ? '' : s.cur.len === 1 ? 'day' : 'days' },
    { x: 548, label: 'LONGEST STREAK', num: String(s.best.len), color: INK, sub: s.placeholder ? 'Syncing on first run' : s.best.len ? `${fmt(s.best.start)} – ${fmt(s.best.end)}` : '—', unit: s.placeholder ? '' : s.best.len === 1 ? 'day' : 'days' },
  ];
  let g = '';
  g += `<rect x="40" y="34" width="9" height="9" fill="${RED}"/>`;
  g += text('GITHUB STREAK · LIVE', 58, 43, 11, 'mono', '#555', { ls: 1.8 });
  g += text(`UPDATED ${fmt(s.today, true).toUpperCase()}`, 748, 43, 11, 'mono', INK, { anchor: 'end', ls: 1.8 });
  cols.forEach((c, i) => {
    if (i) g += `<line x1="${c.x - 22}" y1="70" x2="${c.x - 22}" y2="150" stroke="${INK}" stroke-opacity=".18" stroke-width="2"/>`;
    g += text(c.label, c.x, 84, 11, 'mono', '#888', { ls: 1.8 });
    g += text(c.num, c.x, 138, 54, 'serif', c.color);
    if (c.unit) g += text(c.unit, c.x + width(c.num, 'serif', 54) + 8, 138, 16, 'serif', '#555');
    g += text(c.sub, c.x, 164, 13, 'sans', '#555');
  });
  g += `<line x1="40" y1="182" x2="748" y2="182" stroke="${INK}" stroke-opacity=".18" stroke-width="2" stroke-dasharray="2 5" stroke-linecap="round"/>`;
  g += text('LAST 30 DAYS', 40, 214, 11, 'mono', '#888', { ls: 1.8 });
  const max = Math.max(1, ...s.last30), x0 = 170, span = 748 - x0, step = span / 30, bw = step - 5;
  s.last30.forEach((v, i) => {
    const h = v ? 6 + (v / max) * 22 : 3;
    g += `<rect x="${(x0 + i * step).toFixed(1)}" y="${(218 - h).toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="1.5" fill="${v ? BLUE : '#E2E0D8'}"${v && i === 29 ? ` stroke="${INK}" stroke-width="1.5"` : ''}/>`;
  });
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="GitHub contributions: ${s.lastYear} in the last year, ${s.cur.len} day current streak, ${s.best.len} day longest streak"><rect x="7" y="7" width="788" height="${CH}" rx="14" fill="${INK}"/><rect x="0" y="0" width="788" height="${CH}" rx="14" fill="#F4F3EE" stroke="${INK}" stroke-width="2.5"/>${g}</svg>\n`;
}

if (!MOCK && !TOKEN && !process.argv.includes('--placeholder')) { console.error('GH_TOKEN / GITHUB_TOKEN is required'); process.exit(1); }
const PLACEHOLDER = process.argv.includes('--placeholder');
const s = PLACEHOLDER
  ? { lastYear: '—', today: iso(new Date()), cur: { len: '—' }, best: { len: '—' }, last30: Array(30).fill(0), placeholder: true }
  : stats(await loadDays());
fs.writeFileSync(OUT, render(s));
console.log(`wrote ${OUT}`, { lastYear: s.lastYear, current: s.cur.len, longest: s.best.len });
