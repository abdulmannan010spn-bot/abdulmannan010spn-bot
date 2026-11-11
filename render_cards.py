#!/usr/bin/env python3
"""Render the profile README as a stack of animated SVG cards.

    python render_cards.py              writes cards/*.svg and refreshes the card
                                        stack inside README.md
    STATIC=1 python render_cards.py     writes the final frame (no animation) to
                                        preview/ plus preview/preview.html, so you
                                        can screenshot it. Nothing in cards/ or
                                        README.md is touched.

Input: data/github.json (written by scripts/fetch_data.sh, refreshed daily by
.github/workflows/profile-cards.yml). Set DATA=path to read another file.

The SVGs are shown through <img>, so they use only what works there: one inline
<style> (CSS keyframes), SMIL <animate>, and fonts subset per card with
pyftsubset and embedded as base64 WOFF. No scripts, no external requests.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import math
import os
import re
import subprocess
import sys
import tempfile
import json
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("preview" if STATIC else "cards")
DATA = Path(os.environ.get("DATA", ROOT / "data" / "github.json"))
README = ROOT / "README.md"
MARK_START, MARK_END = "<!-- cards:start -->", "<!-- cards:end -->"

# --------------------------------------------------------------------------
# Profile: edit this block. Everything else is derived from it and the data.
# --------------------------------------------------------------------------
BLUE, NAVY, SKY = "#2563EB", "#1E3A8A", "#60A5FA"  # from the old README's badges and banner

PROFILE = {
    "login": "abdulmannan010spn-bot",
    "name": "Abdul Mannan",  # first word upright, the rest italic
    "label": "Frontend developer at OSS, AKGEC",
    "tagline": "I design and build responsive, interactive web interfaces.",
    "site_label": "github.com/abdulmannan010spn-bot",
    "graph_title": ("Every commit, ", "in blue"),
    "stats_label": "The last 12 months on GitHub",
    "repos_title": ("What I\u2019m ", "building"),
    # Optional portrait, drawn as ASCII art: drop assets/portrait.jpg (or .png/.webp).
    # A head-and-shoulders shot against a plain light background works best.
    "portrait_candidates": ["portrait.jpg", "portrait.jpeg", "portrait.png", "portrait.webp"],
    "max_repos": 3,
    "tiles": [
        # TODO: replace the placeholder portfolio URL.
        {"slug": "portfolio", "label": "See", "runs": [("The ", "s"), ("work", "si")],
         "desc": "Projects, case studies and design work.",
         "url": "https://yourportfolio.com", "link": "portfolio", "bg": BLUE, "fg": "#FFFFFF"},
        {"slug": "linkedin", "label": "Connect", "runs": [("Let\u2019s ", "s"), ("talk", "si")],
         "desc": "Open to frontend internships, open source and freelance projects.",
         "url": "https://linkedin.com/in/abdul-mannan-537494375", "link": "linkedin",
         "bg": NAVY, "fg": "#FFFFFF"},
        {"slug": "email", "label": "Write", "runs": [("Say ", "s"), ("hello", "si")],
         "desc": "abdulmannan010spn@gmail.com",
         "url": "mailto:abdulmannan010spn@gmail.com", "link": "email", "bg": SKY, "fg": "#0F172A"},
    ],
}

# Palette: warm paper cards on GitHub's dark page, one blue accent.
PAPER, INK, MUTED, HAIR, EDGE = "#F3F1EC", "#141414", "#6F6C66", "#DAD7CF", "#E4E1DA"
ACCENT = BLUE
PARTIAL = "#C9C6BE"  # the month that is still in progress
LEVELS = ["#DCE1EE", "#BFD3FB", "#8DB1F8", "#3B82F6", "#1D4ED8"]  # graph, empty -> busiest

W = 888  # GitHub's README column is about this wide
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# face key -> (family, weight, style, file)
FACES = {
    "s": ("Instrument Serif", 400, "normal", "InstrumentSerif-Regular.woff"),
    "si": ("Instrument Serif", 400, "italic", "InstrumentSerif-Italic.woff"),
    "n": ("Inter", 400, "normal", "Inter-Regular.woff"),
    "nb": ("Inter", 600, "normal", "Inter-SemiBold.woff"),
    "m": ("JetBrains Mono", 400, "normal", "JetBrainsMono-Regular.woff"),
}
FACE_CSS = (
    ".s{font-family:'Instrument Serif',Georgia,serif}"
    ".si{font-family:'Instrument Serif',Georgia,serif;font-style:italic}"
    ".n{font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif}"
    ".nb{font-family:Inter,system-ui,-apple-system,'Segoe UI',sans-serif;font-weight:600}"
    ".m{font-family:'JetBrains Mono',ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}"
    ".a{white-space:pre}"
)
_metrics: dict[str, tuple] = {}


def metrics(face: str):
    if face not in _metrics:
        f = TTFont(ROOT / "assets" / "fonts" / FACES[face][3])
        _metrics[face] = (f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm)
    return _metrics[face]


def measure(face: str, text: str, size: float, ls: float = 0.0) -> float:
    cmap, hmtx, upm = metrics(face)
    return sum(hmtx[cmap[ord(c)]][0] / upm * size + ls for c in text if ord(c) in cmap)


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def n(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def clean(s: str, face: str = "n") -> str:
    """Keep only characters the face has a glyph for (drops emoji etc.)."""
    cmap = metrics(face)[0]
    return "".join(ch if ord(ch) in cmap else " " if ch.isspace() else "" for ch in s)


def wrap_px(face: str, text: str, size: float, max_w: float, max_lines: int) -> list[str]:
    lines, cur = [], ""
    for word in clean(text, face).split():
        trial = f"{cur} {word}".strip()
        if measure(face, trial, size) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and measure(face, last + "\u2026", size) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "\u2026"
    return lines


def fmt_day(d: dt.date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


# --------------------------------------------------------------------------
# Fonts: subset per card with pyftsubset, embed as base64 WOFF
# --------------------------------------------------------------------------
_face_cache: dict[tuple[str, str], str] = {}


def font_face(face: str, chars: set[str]) -> str:
    chars_s = "".join(sorted(chars | {" "}))
    key = (face, chars_s)
    if key not in _face_cache:
        family, weight, style, fname = FACES[face]
        with tempfile.TemporaryDirectory() as tmp:
            txt, out = Path(tmp, "chars.txt"), Path(tmp, "f.woff")
            txt.write_text(chars_s, encoding="utf-8")
            subprocess.run(
                ["pyftsubset", str(ROOT / "assets" / "fonts" / fname), f"--text-file={txt}",
                 "--flavor=woff", f"--output-file={out}", "--layout-features=kern",
                 "--no-hinting", "--notdef-outline"],
                check=True,
            )
            b64 = base64.b64encode(out.read_bytes()).decode()
        _face_cache[key] = (
            f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};"
            f"src:url(data:font/woff;base64,{b64}) format('woff')}}"
        )
    return _face_cache[key]


# --------------------------------------------------------------------------
# Animation plumbing. Every animated property rests at its final value, so the
# keyframes only declare the *from* state. `animation: none` therefore lands on
# the final frame, which is exactly what reduced-motion and STATIC need.
# SMIL cannot be switched off from CSS, so SMIL-only decoration lives in .decor
# (hidden under reduced motion) and the wiped-in name has a .live / .still pair.
# --------------------------------------------------------------------------
ANIM_CSS = """
@keyframes fade{from{opacity:0}}
@keyframes rise{from{opacity:0;transform:translateY(10px)}}
@keyframes grow{from{transform:scaleY(0)}}
.fade{animation:fade .5s ease-out var(--d,0s) backwards}
.rise{animation:rise .6s cubic-bezier(.2,.7,.2,1) var(--d,0s) backwards}
.grow{transform-box:fill-box;transform-origin:50% 100%;animation:grow .7s cubic-bezier(.2,.7,.2,1) var(--d,0s) backwards}
.still{display:none}
@media (prefers-reduced-motion:reduce){
*{animation:none!important}
.live,.decor{display:none}
.still{display:inline}
}
"""


def anim(cls: str, delay: float) -> str:
    return "" if STATIC else f' class="{cls}" style="--d:{n(delay)}s"'


class Card:
    def __init__(self, name: str, w: int, h: int, title: str, start: float = 0.0):
        self.name, self.w, self.h, self.title, self.start = name, w, h, title, start
        self.defs: list[str] = []
        self.css: list[str] = []
        self.body: list[str] = []
        self.chars: dict[str, set[str]] = {k: set() for k in FACES}

    def add(self, *parts: str) -> None:
        self.body.extend(parts)

    def t(self, x, y, s, size, face="n", fill=INK, anchor="start", ls=0.0, extra="") -> str:
        s = clean(s, face)
        self.chars[face].update(s)
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        l = f' letter-spacing="{n(ls)}"' if ls else ""
        return (f'<text class="{face}" x="{n(x)}" y="{n(y)}" font-size="{n(size)}" '
                f'fill="{fill}"{a}{l} xml:space="preserve"{extra}>{esc(s)}</text>')

    def runs(self, x, y, parts, size, fill=INK) -> str:
        """One line made of runs in different faces: [(text, face[, fill])]."""
        spans = []
        for p in parts:
            txt, face = clean(p[0], p[1]), p[1]
            self.chars[face].update(txt)
            f = f' fill="{p[2]}"' if len(p) > 2 else ""
            spans.append(f'<tspan class="{face}"{f}>{esc(txt)}</tspan>')
        return (f'<text x="{n(x)}" y="{n(y)}" font-size="{n(size)}" fill="{fill}" '
                f'xml:space="preserve">{"".join(spans)}</text>')

    def svg(self) -> str:
        faces = "".join(font_face(k, ch) for k, ch in self.chars.items() if ch)
        css = FACE_CSS + "".join(self.css) + ("" if STATIC else ANIM_CSS)
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
            f'width="{self.w}" height="{self.h}" role="img" aria-label="{esc(self.title)}">'
            f"<title>{esc(self.title)}</title><style>{faces}{css}</style>{defs}"
            f"<g{anim('fade', self.start)}>{''.join(self.body)}</g></svg>\n"
        )


def paper(c: Card, fill=PAPER, rx=14) -> str:
    return (f'<rect x=".5" y=".5" width="{c.w - 1}" height="{c.h - 1}" rx="{rx}" '
            f'fill="{fill}" stroke="{EDGE}"/>')


def arrow(x: float, y: float, color: str, delay: float) -> str:
    return (f'<g transform="translate({n(x)} {n(y)})"><path{anim("fade", delay)} '
            f'd="M0 9 L9 0 M2.5 0 H9 V6.5" fill="none" stroke="{color}" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round"/></g>')


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def load_data() -> dict:
    if not DATA.exists():
        sys.exit(f"{DATA} not found. Run scripts/fetch_data.sh first, or set DATA=data/github.sample.json.")
    data = json.loads(DATA.read_text(encoding="utf-8"))
    if data.get("_sample"):
        print("note: rendering SAMPLE data, not your real contributions", file=sys.stderr)
    return data


def analyse(data: dict):
    days: dict[dt.date, int] = {}
    for week in data["weeks"]:
        for d in week:
            days[dt.date.fromisoformat(d["date"])] = int(d["count"])
    dates = sorted(days)
    counts = [days[d] for d in dates]

    i = len(dates) - 1
    if i >= 0 and counts[i] == 0:  # today not contributed yet: the streak is still alive
        i -= 1
    streak = 0
    while i >= 0 and counts[i] > 0:
        streak += 1
        i -= 1
    longest = run = 0
    for c in counts:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)

    best = max(range(len(dates)), key=lambda k: (counts[k], k)) if dates else 0
    per_month: dict[tuple[int, int], int] = {}
    for d, c in days.items():
        per_month[(d.year, d.month)] = per_month.get((d.year, d.month), 0) + c
    months = [(MONTHS[m - 1], per_month[(y, m)]) for (y, m) in sorted(per_month)][-12:]

    return {
        "days": days, "dates": dates, "counts": counts, "total": sum(counts),
        "streak": streak, "longest": longest, "active": sum(1 for c in counts if c > 0),
        "span": len(dates), "months": months,
        "busiest_day": dates[best] if dates else None,
        "busiest_n": counts[best] if dates else 0,
    }


def pick_repos(data: dict) -> list[dict]:
    login = PROFILE["login"].lower()
    pool = data.get("pinned") or data.get("top") or []
    return [r for r in pool if r["name"].lower() != login][: PROFILE["max_repos"]]


# --------------------------------------------------------------------------
# Timeline: one global clock so the page plays top to bottom.
# --------------------------------------------------------------------------
def timeline(n_repos: int) -> dict:
    return {"hero": 0.0, "graph": 1.7, "activity": 2.5, "tiles": 3.3, "heading": 4.0, "repos": 4.3}


# --------------------------------------------------------------------------
# Hero
# --------------------------------------------------------------------------
def ascii_portrait(cols: int, rows: int, cw: float, ch: float) -> list[str] | None:
    for name in PROFILE["portrait_candidates"]:
        p = ROOT / "assets" / name
        if not p.exists():
            continue
        try:
            from PIL import Image, ImageOps
        except ImportError:
            print("note: install pillow to render the ASCII portrait", file=sys.stderr)
            return None
        im = ImageOps.autocontrast(Image.open(p).convert("L"), cutoff=2)
        aspect = (cols * cw) / (rows * ch)
        im = ImageOps.fit(im, (400, int(400 / aspect))).resize((cols, rows), Image.LANCZOS)
        ramp = "@%#*+=-:. "  # dark -> light, drawn in ink on paper
        px = list(im.getdata())
        out = []
        for r in range(rows):
            row = px[r * cols:(r + 1) * cols]
            out.append("".join(ramp[min(len(ramp) - 1, int((v / 255) ** 0.85 * len(ramp)))] for v in row).rstrip())
        return out
    return None


def card_hero(t0: float) -> Card:
    H = 400
    name, tagline = PROFILE["name"], PROFILE["tagline"]
    c = Card("hero", W, H, f"{name}. {PROFILE['label']}. {tagline}", t0)
    c.add(paper(c))

    # label with a pulsing square
    c.add(f'<rect x="36" y="42" width="8" height="8" rx="1.5" fill="{ACCENT}"/>')
    if not STATIC:
        c.add(f'<rect class="decor" x="36" y="42" width="8" height="8" rx="1.5" fill="{ACCENT}">'
              f'<animate attributeName="opacity" values="1;.25;1" dur="2.4s" begin="{n(t0 + 1.5)}s" repeatCount="indefinite"/></rect>')
    c.add(c.t(54, 51, PROFILE["label"].upper(), 11, "m", MUTED, ls=1.2))

    # ASCII portrait on the right, revealed row by row
    cell_w, cell_h, size = 4.44, 8.2, 7.4
    cols, rows = 70, 41
    art = ascii_portrait(cols, rows, cell_w, cell_h)
    if art:
        x_art = W - 36 - cols * cell_w
        for i, line in enumerate(art):
            if not line.strip():
                continue
            c.chars["m"].update(line)
            c.add(f'<g{anim("fade", t0 + 0.4 + i * 0.022)}><text class="m a" x="{n(x_art)}" y="{n(44 + i * cell_h)}" '
                  f'font-size="{size}" fill="#3B3A36" xml:space="preserve">{esc(line)}</text></g>')

    # Name: first word upright, the rest italic. Each line wipes in left to right.
    words = name.split()
    lines = [(words[0], "s")] + ([(" ".join(words[1:]), "si")] if len(words) > 1 else [])
    S, x0, base0, lead = 124, 36, 176, 100
    avail = (W - 36 - cols * cell_w - 24 - x0) if art else (W - 2 * x0)
    widest = max(measure(f, t, S) for t, f in lines)
    if widest > avail:
        S = S * avail / widest
    begin = t0 + 0.25
    for i, (txt, face) in enumerate(lines):
        base = base0 + i * lead * S / 124
        el = c.t(x0, base, txt, S, face, INK, ls=-1.5)
        if STATIC:
            c.add(f'<g class="still">{el}</g>')
            continue
        wd = measure(face, txt, S, -1.5) + 48
        b = begin + i * 0.55
        c.defs.append(
            f'<clipPath id="w{i}"><rect x="{x0 - 16}" y="{n(base - S * 0.85)}" width="0" height="{n(S * 1.15)}">'
            f'<animate attributeName="width" from="0" to="{n(wd)}" dur="0.9s" begin="{n(b)}s" '
            f'calcMode="spline" keyTimes="0;1" keySplines=".2 .7 .2 1" fill="freeze"/></rect></clipPath>'
        )
        c.add(f'<g class="live"><g clip-path="url(#w{i})">{el}</g></g><g class="still">{el}</g>')
    done = begin + 0.55 * (len(lines) - 1) + 0.9

    c.add(f'<g{anim("rise", done - 0.3)}>{c.t(x0, 326, tagline, 17, "n", INK)}'
          f'{c.t(x0, 358, PROFILE["site_label"], 12, "m", MUTED)}</g>')
    sw = measure("m", PROFILE["site_label"], 12)
    c.add(arrow(x0 + sw + 8, 349, MUTED, done))
    return c


# --------------------------------------------------------------------------
# Contribution graph: "Every commit, in blue"
# --------------------------------------------------------------------------
def card_graph(t0: float, data: dict, a: dict) -> Card:
    weeks = data["weeks"]
    nw = max(1, len(weeks))
    pad, gap = 36, 3
    cell = min(14.0, (W - 2 * pad - gap * (nw - 1)) / nw)
    step = cell + gap
    y0 = 90
    H = int(y0 + 7 * step - gap + 46)
    c = Card("graph", W, H,
             f"Contribution graph for the last year: {a['total']:,} contributions on {a['active']} days.", t0)
    c.css.append("".join(f".l{k}{{fill:{col}}}" for k, col in enumerate(LEVELS)))
    c.add(paper(c))
    a1, a2 = PROFILE["graph_title"]
    c.add(c.runs(pad, 60, [(a1, "s"), (a2, "si", ACCENT)], 34))
    c.add(c.t(W - pad, 50, "REFRESHED DAILY", 10.5, "m", MUTED, "end", 1.2))

    nz = sorted(v for v in a["counts"] if v > 0)

    def q(p: float) -> int:
        return nz[min(len(nz) - 1, int(p * len(nz)))] if nz else 0

    t1, t2, t3 = q(0.25), q(0.5), q(0.75)

    def level(v: int) -> int:
        return 0 if v == 0 else 1 + (v > t1) + (v > t2) + (v > t3)

    for i, w in enumerate(weeks):
        cells = []
        for d in w:
            date = dt.date.fromisoformat(d["date"])
            row = (date.weekday() + 1) % 7
            cells.append(f'<rect class="l{level(int(d["count"]))}" y="{n(y0 + row * step)}" '
                         f'width="{n(cell)}" height="{n(cell)}" rx="2.5"/>')
        c.add(f'<g{anim("fade", t0 + 0.25 + i * 0.018)} transform="translate({n(pad + i * step)} 0)">{"".join(cells)}</g>')

    ylab = y0 + 7 * step - gap + 22
    marks, prev = [], None
    for i, w in enumerate(weeks):
        mo = dt.date.fromisoformat(w[0]["date"]).month
        if mo != prev:
            marks.append((i, mo))
            prev = mo
    marks = [m for k, m in enumerate(marks) if k == len(marks) - 1 or marks[k + 1][0] - m[0] >= 3]
    # legend: less [] [] [] [] [] more, right aligned on the label row
    mw = measure("m", "more", 10.5)
    lx = W - pad - mw
    legend_left = lx - 8 - 5 * 13 - 6 - measure("m", "less", 10.5)
    for i, mo in marks:  # month labels must not run into the legend
        x = pad + i * step
        if x + measure("m", MONTHS[mo - 1], 10.5) < legend_left - 16:
            c.add(c.t(x, ylab, MONTHS[mo - 1], 10.5, "m", MUTED))
    c.add(c.t(W - pad, ylab, "more", 10.5, "m", MUTED, "end"))
    for k in range(5):
        c.add(f'<rect x="{n(lx - 8 - (5 - k) * 13)}" y="{n(ylab - 9)}" width="10" height="10" rx="2.5" fill="{LEVELS[k]}"/>')
    c.add(c.t(lx - 8 - 5 * 13 - 6, ylab, "less", 10.5, "m", MUTED, "end"))
    return c


# --------------------------------------------------------------------------
# Stats: big total, monthly bars, three numbers
# --------------------------------------------------------------------------
def card_activity(t0: float, a: dict) -> Card:
    H = 330
    c = Card("activity", W, H, (
        f"{a['total']:,} contributions in the last 12 months, shown per month. Current streak {a['streak']} days "
        f"(longest {a['longest']}), {a['active']} of {a['span']} days with a commit, busiest day "
        f"{fmt_day(a['busiest_day']) if a['busiest_day'] else 'none'} with {a['busiest_n']} contributions."), t0)
    c.add(paper(c))
    c.add(c.t(36, 52, PROFILE["stats_label"].upper(), 11, "m", MUTED, ls=1.2))

    size = 58.0
    big = [(f"{a['total']:,} ", "s"), ("contributions", "si")]
    wbig = sum(measure(f, t, size) for t, f in big)
    if wbig > 560:
        size *= 560 / wbig
    c.add(f'<g{anim("rise", t0 + 0.1)}>{c.runs(36, 116, big, size)}</g>')

    months = a["months"]
    peak = max((v for _, v in months), default=0) or 1
    peak_i = max(range(len(months)), key=lambda i: (months[i][1], i)) if months else -1
    base, top, x_lo, x_hi = 278, 118, 36, 600
    slot = (x_hi - x_lo) / max(1, len(months))
    bw = slot * 0.74
    for i, (label, v) in enumerate(months):
        h = max(3, round(v / peak * top)) if v else 2
        x = x_lo + i * slot + (slot - bw) / 2
        d = t0 + 0.4 + i * 0.05
        col = ACCENT if i == peak_i else PARTIAL if i == len(months) - 1 else INK
        c.add(f'<rect{anim("grow", d)} x="{n(x)}" y="{base - h}" width="{n(bw)}" height="{h}" rx="3" fill="{col}"/>')
        if i == peak_i:
            c.add(f'<g{anim("fade", d + 0.4)}>{c.t(x + bw / 2, base - h - 8, f"{v:,}", 10.5, "m", ACCENT, "middle")}</g>')
        c.add(c.t(x + bw / 2, 302, label, 10.5, "m", MUTED, "middle"))

    col_x, col_r = 648, W - 36
    blocks = [
        ([(f"{a['streak']} ", "s"), ("days", "si")], f"current streak, longest {a['longest']}"),
        ([(f"{a['active']}", "s"), (f" / {a['span']}", "s", "#B3B0A8")], "days with a commit"),
        ([(f"{a['busiest_n']}", "s")],
         f"on the busiest day, {fmt_day(a['busiest_day'])}" if a["busiest_day"] else "on the busiest day"),
    ]
    for i, (num, cap) in enumerate(blocks):
        y = 92 + i * 90
        c.add(f'<g{anim("rise", t0 + 0.3 + i * 0.12)}>{c.runs(col_x, y, num, 46)}{c.t(col_x, y + 22, cap, 12.5, "n", MUTED)}</g>')
        if i < 2:
            c.add(f'<path d="M{col_x} {y + 44}H{col_r}" stroke="{HAIR}" stroke-width="1"/>')
    return c


# --------------------------------------------------------------------------
# Tiles (3 across), repo heading and repo cards (3 across)
# --------------------------------------------------------------------------
def card_tile(tile: dict, t0: float) -> Card:
    TW, TH, pad = 300, 216, 24
    desc_text = tile["desc"]
    c = Card(f"tile-{tile['slug']}", TW, TH,
             f"{''.join(r[0] for r in tile['runs'])}: {desc_text}", t0)
    fg = tile["fg"]
    c.add(f'<rect width="{TW}" height="{TH}" rx="14" fill="{tile["bg"]}"/>')
    big = tile["runs"]
    size = 46.0
    wb = sum(measure(f, t, size) for t, f in big)
    if wb > TW - 2 * pad:
        size *= (TW - 2 * pad) / wb
    body = [c.t(pad, 38, tile["label"].upper(), 10.5, "m", fg, ls=1.2, extra=' fill-opacity=".85"'),
            c.runs(pad, 112, big, size, fg)]
    for k, line in enumerate(wrap_px("n", desc_text, 13, TW - 2 * pad, 3)):
        body.append(c.t(pad, 150 + k * 19, line, 13, "n", fg, extra=' fill-opacity=".92"'))
    c.add(f'<g{anim("rise", t0 + 0.1)}>{"".join(body)}</g>')
    c.add(arrow(TW - pad - 9, 28, fg, t0 + 0.4))
    return c


def card_heading(t0: float) -> Card:
    c = Card("heading", W, 92, "".join(PROFILE["repos_title"]), t0)
    c.add(paper(c))
    a1, a2 = PROFILE["repos_title"]
    c.add(f'<g{anim("rise", t0 + 0.1)}>{c.runs(36, 62, [(a1, "s"), (a2, "si", ACCENT)], 42)}</g>')
    return c


def star_points(cx: float, cy: float, r: float) -> str:
    return " ".join(
        f"{n(cx + (r if k % 2 == 0 else r * 0.45) * math.sin(k * math.pi / 5))},"
        f"{n(cy - (r if k % 2 == 0 else r * 0.45) * math.cos(k * math.pi / 5))}" for k in range(10))


def card_repo(repo: dict, t0: float) -> Card:
    RW, RH, pad = 300, 196, 24
    desc = repo.get("description") or "No description yet."
    c = Card(f"repo-{repo['name']}", RW, RH,
             f"{repo['name']}: {desc} {repo.get('stars', 0)} stars.", t0)
    color = repo.get("color") or MUTED
    c.add(paper(c))
    body = [f'<circle cx="{pad + 4}" cy="38" r="4" fill="{color}"/>',
            c.t(pad + 16, 43, repo["name"], 15, "nb", INK)]
    for k, line in enumerate(wrap_px("n", desc, 13, RW - 2 * pad, 4)):
        body.append(c.t(pad, 76 + k * 19, line, 13, "n", "#55524C"))
    x, y = pad, RH - 24
    lang = repo.get("language")
    if lang:
        body.append(c.t(x, y, lang, 11, "m", MUTED))
        x += measure("m", lang, 11)
        body.append(c.t(x, y, " \u00b7 ", 11, "m", MUTED))
        x += measure("m", " \u00b7 ", 11)
    body.append(f'<polygon points="{star_points(x + 5, y - 4, 5.5)}" fill="none" stroke="{MUTED}" '
                f'stroke-width="1.1" stroke-linejoin="round"/>')
    body.append(c.t(x + 15, y, str(repo.get("stars", 0)), 11, "m", MUTED))
    c.add(f'<g{anim("rise", t0 + 0.1)}>{"".join(body)}</g>')
    return c


# --------------------------------------------------------------------------
# README stack + preview page
# --------------------------------------------------------------------------
def repo_file(r: dict) -> str:
    return "repo-" + re.sub(r"[^A-Za-z0-9._-]", "-", r["name"]) + ".svg"


def stack_html(prefix: str, repos: list[dict], alts: dict) -> str:
    a = lambda s: html.escape(s, quote=True)
    p = lambda inner: f'<p align="center">{inner}</p>'
    img = lambda f, alt, w="100%": f'<img src="{prefix}{f}" alt="{a(alt)}" width="{w}">'
    out = ['<div align="center">', "", p(img("hero.svg", alts["hero"])),
           p(img("graph.svg", alts["graph"])), p(img("activity.svg", alts["activity"]))]
    tiles = [f'<a href="{a(t["url"])}">{img("tile-" + t["slug"] + ".svg", alts["tile-" + t["slug"]], "32.5%")}</a>'
             for t in PROFILE["tiles"]]
    out.append(p("\n".join(tiles)))
    if repos:
        out.append(p(img("heading.svg", alts["heading"])))
        cards = [f'<a href="{a(r["url"])}">{img(repo_file(r), alts[repo_file(r)], "32.5%")}</a>' for r in repos]
        out.append(p("\n".join(cards)))
    out.append(p(" / ".join(f'<a href="{a(t["url"])}">{a(t["link"])}</a>' for t in PROFILE["tiles"])))
    out += ["", "</div>"]
    return "\n".join(out)


def update_readme(block: str) -> None:
    text = README.read_text(encoding="utf-8") if README.exists() else f"{MARK_START}\n{MARK_END}\n"
    if MARK_START not in text or MARK_END not in text:
        sys.exit(f"README.md needs the markers {MARK_START} and {MARK_END}")
    head, rest = text.split(MARK_START, 1)
    _, tail = rest.split(MARK_END, 1)
    README.write_text(f"{head}{MARK_START}\n{block}\n{MARK_END}{tail}", encoding="utf-8")


def main() -> None:
    data = load_data()
    a = analyse(data)
    repos = pick_repos(data)
    tl = timeline(len(repos))

    cards: dict[str, Card] = {"hero": card_hero(tl["hero"]), "graph": card_graph(tl["graph"], data, a),
                              "activity": card_activity(tl["activity"], a)}
    for i, tile in enumerate(PROFILE["tiles"]):
        cards[f"tile-{tile['slug']}"] = card_tile(tile, tl["tiles"] + i * 0.15)
    if repos:
        cards["heading"] = card_heading(tl["heading"])
    for i, r in enumerate(repos):
        cards[repo_file(r)[:-4]] = card_repo(r, tl["repos"] + i * 0.15)

    OUT.mkdir(exist_ok=True)
    for stale in OUT.glob("*.svg"):
        stale.unlink()
    for name, card in cards.items():
        svg = card.svg()
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
        print(f"{OUT.name}/{name}.svg  {len(svg) / 1024:.1f} KB")

    alts = {k: v.title for k, v in cards.items()}
    alts.update({repo_file(r): cards[repo_file(r)[:-4]].title for r in repos})
    block = stack_html("" if STATIC else "cards/", repos, alts)
    if STATIC:
        bg = "#0d1117" if os.environ.get("THEME", "dark") == "dark" else "#ffffff"
        (OUT / "preview.html").write_text(
            f'<!doctype html><meta charset="utf-8"><title>Profile preview</title>'
            f'<style>body{{margin:0;background:{bg};color:#9198a1;font:14px system-ui}}'
            f'main{{max-width:824px;margin:24px auto}}main img{{vertical-align:top}}p{{margin:0 0 16px;text-align:center}}'
            f'a{{color:#4493f8}}</style><main>{block}</main>', encoding="utf-8")
        print(f"open {OUT.name}/preview.html to screenshot the final frame")
    else:
        update_readme(block)
        print("README.md card stack updated")


if __name__ == "__main__":
    main()
