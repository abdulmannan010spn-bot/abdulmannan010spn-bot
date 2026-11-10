#!/usr/bin/env python3
"""Render the GitHub profile README cards as animated, self-contained SVGs ("paper" style).

    python scripts/render_cards.py            -> cards/         (animated, committed by the Action)
    STATIC=1 python scripts/render_cards.py   -> cards-static/  (final frame + preview.html for screenshots)

GitHub shows the cards through <img> tags, so each SVG carries everything it needs:
  * CSS keyframes in an inline <style> block (content reveals; they stop under
    prefers-reduced-motion, which leaves the final frame on screen)
  * SMIL <animate> elements, used only for decorative extras inside <g class="deco">,
    which the reduced-motion rule hides (CSS cannot pause SMIL)
  * fonts subset with pyftsubset to the glyphs each card uses, embedded as base64 WOFF

Data comes from data/github.json, refreshed daily by .github/workflows/profile-cards.yml.
Optional: put a photo at assets/portrait.png|jpg|webp and the hero draws it as ASCII art.
"""
from __future__ import annotations

import base64
import datetime as dt
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
STATIC = os.environ.get("STATIC", "").strip().lower() not in ("", "0", "false", "no")
DATA_FILE = ROOT / "data" / "github.json"
FONT_DIR = ROOT / "fonts"
ASSET_DIR = ROOT / "assets"
OUT_DIR = ROOT / ("cards-static" if STATIC else "cards")

# --------------------------------------------------------------------------- profile
NAME = "Abdul Mannan"
KICKER = "FRONTEND DEVELOPER & UI/UX DESIGNER"
HERO_LINE = "I design and build responsive, interactive web interfaces for the Open Source Software Society."
SITE_URL = "https://yourportfolio.com"  # TODO: replace with your real portfolio URL
LINKEDIN_URL = "https://linkedin.com/in/abdul-mannan-537494375"
EMAIL = "abdulmannan010spn@gmail.com"
GITHUB_LOGIN = "abdulmannan010spn-bot"
MAX_REPOS = 6

# Tiles: (file, kicker, big roman word, big italic word, blurb, background, text colour, link)
TILES = [
    ("tile-portfolio.svg", "PORTFOLIO", "My ", "work", "Projects, case studies and UI experiments.", "blue", "#fff", SITE_URL),
    ("tile-linkedin.svg", "LINKEDIN", "Let's ", "connect", "Experience, education and what I'm learning.", "red", "#fff", LINKEDIN_URL),
    ("tile-email.svg", "EMAIL", "Say ", "hello", "Open to internships, open source and freelance work.", "yellow", "#111114", f"mailto:{EMAIL}"),
]

# --------------------------------------------------------------------------- style
P = dict(
    paper="#F4F2ED", ink="#111114", mute="#6B6B72", faint="#A9A7A0", line="#D9D6CE", white="#FAFAF8",
    blue="#2F45FF", red="#F03A2B", yellow="#FFC61A", grey="#CFCCC4",
)
LEVELS = ["#DEDBD4", "#C9D0FF", "#93A2FF", "#5468FF", "#2336F0"]  # contribution ramp, light to brand blue

# class -> (file, weight, family, style)
FACES = {
    "m": ("FiraCode-Regular.ttf", 400, "FC", "normal"),
    "mb": ("FiraCode-SemiBold.ttf", 600, "FC", "normal"),
    "s": ("Inter-Regular.otf", 400, "IN", "normal"),
    "sb": ("Inter-SemiBold.otf", 600, "IN", "normal"),
    "d": ("InstrumentSerif-Regular.ttf", 400, "DS", "normal"),
    "di": ("InstrumentSerif-Italic.ttf", 400, "DS", "italic"),
}
FAMILY_STACK = {
    "FC": "FC,'Fira Code',ui-monospace,Menlo,Consolas,monospace",
    "IN": "IN,Inter,system-ui,-apple-system,'Segoe UI',sans-serif",
    "DS": "DS,'Instrument Serif',Georgia,'Times New Roman',serif",
}

# Seconds each block starts at, so the page plays top to bottom.
T_HERO, T_GRAPH, T_STATS, T_TILES, T_HEAD, T_REPOS = 0.0, 1.0, 1.8, 2.6, 3.2, 3.4
GAP = 12  # transparent strip under every card so stacked images don't touch
MON = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def esc(s) -> str:
    return html.escape(str(s), quote=True)


# --------------------------------------------------------------------------- fonts
_faces: dict[str, TTFont] = {}


def _face(key: str) -> TTFont:
    if key not in _faces:
        path = FONT_DIR / FACES[key][0]
        if not path.exists():
            sys.exit(f"missing font file: {path}")
        _faces[key] = TTFont(path)
    return _faces[key]


def text_width(key: str, text: str, size: float) -> float:
    f = _face(key)
    cmap, hmtx, upm = f.getBestCmap(), f["hmtx"], f["head"].unitsPerEm
    total = sum(hmtx[cmap[ord(c)]][0] if ord(c) in cmap else upm * 0.5 for c in text)
    return total * size / upm * 1.02  # small safety margin (no kerning applied)


def fit_size(key: str, text: str, max_w: float, size: float, min_size: float) -> float:
    while size > min_size and text_width(key, text, size) > max_w:
        size -= 0.5
    return size


def ellipsize(key: str, text: str, max_w: float, size: float) -> str:
    if text_width(key, text, size) <= max_w:
        return text
    while text and text_width(key, text + "…", size) > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def wrap(key: str, text: str, max_w: float, size: float, max_lines: int) -> list[str]:
    lines, cur = [], ""
    words = text.split()
    for i, w in enumerate(words):
        trial = f"{cur} {w}".strip()
        if text_width(key, trial, size) <= max_w:
            cur = trial
            continue
        lines.append(cur)
        cur = w
        if len(lines) == max_lines:
            break
    if len(lines) < max_lines:
        lines.append(cur)
        return [l for l in lines if l]
    consumed = len(" ".join(lines).split())
    rest = " ".join(words[consumed:])
    lines[-1] = ellipsize(key, f"{lines[-1]} {rest}".strip(), max_w, size)
    return lines[:max_lines]


def _subset_cmd() -> list[str]:
    exe = shutil.which("pyftsubset")
    return [exe] if exe else [sys.executable, "-m", "fontTools.subset"]


def subset_b64(key: str, chars: set[str]) -> str:
    src = FONT_DIR / FACES[key][0]
    with tempfile.TemporaryDirectory() as td:
        txt, out = Path(td) / "glyphs.txt", Path(td) / "out.woff"
        txt.write_text("".join(sorted(c for c in chars if ord(c) >= 32)), encoding="utf-8")
        cmd = _subset_cmd() + [
            str(src), f"--text-file={txt}", "--flavor=woff", "--layout-features=kern",
            "--no-hinting", f"--output-file={out}",
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return base64.b64encode(out.read_bytes()).decode()


# --------------------------------------------------------------------------- svg scaffolding
def anim_css() -> str:
    if STATIC:
        return ""
    return """
.in{animation:rise .7s cubic-bezier(.2,.7,.2,1) backwards;animation-delay:calc(var(--base,0s) + var(--i,0)*.12s)}
.row{animation:fade .5s ease-out backwards;animation-delay:calc(var(--base,0s) + .25s + var(--i,0)*.025s)}
.ul{transform-box:fill-box;transform-origin:0 50%;animation:growx .8s cubic-bezier(.2,.7,.2,1) backwards;animation-delay:calc(var(--base,0s) + .45s)}
.bar{transform-box:fill-box;transform-origin:50% 100%;animation:growy .8s cubic-bezier(.2,.7,.2,1) backwards;animation-delay:calc(var(--base,0s) + .4s + var(--i,0)*.06s)}
.lab{animation:fade .5s ease-out backwards;animation-delay:calc(var(--base,0s) + 1.1s)}
.c{transform-box:fill-box;transform-origin:center;animation:pop .4s cubic-bezier(.2,.9,.3,1.2) backwards;animation-delay:calc(var(--base,0s) + .3s + var(--i,0)*.02s)}
@keyframes rise{from{opacity:0;transform:translateY(12px)}}
@keyframes growx{from{transform:scaleX(0)}}
@keyframes growy{from{transform:scaleY(0)}}
@keyframes fade{from{opacity:0}}
@keyframes pop{from{opacity:0;transform:scale(.3)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.deco{display:none}}
"""


def deco(markup: str) -> str:
    """Decorative SMIL group. Omitted in STATIC mode, hidden under prefers-reduced-motion."""
    return "" if STATIC else f'<g class="deco">{markup}</g>'


def svg_doc(w: int, h: int, body: str, title: str, desc: str, base: float = 0.0) -> str:
    chars = set("0123456789 .,")
    for m in re.finditer(r">([^<>]+)<", body):
        chars.update(html.unescape(m.group(1)))
    tokens = {t for cls in re.findall(r'class="([^"]*)"', body) for t in cls.split()}
    used = [k for k in FACES if k in tokens]
    css = ""
    for key in used:
        _, weight, fam, style = FACES[key]
        css += (f"@font-face{{font-family:{fam};font-weight:{weight};font-style:{style};"
                f"src:url(data:font/woff;base64,{subset_b64(key, chars)}) format(\"woff\")}}")
    css += "text{font-variant-ligatures:none;font-kerning:normal}"
    for key in used:
        _, weight, fam, style = FACES[key]
        css += f".{key}{{font-family:{FAMILY_STACK[fam]};font-weight:{weight};font-style:{style}}}"
    css += anim_css()
    style_attr = "" if STATIC else f' style="--base:{base:.2f}s"'
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h + GAP}" viewBox="0 0 {w} {h + GAP}" '
        f'role="img" aria-labelledby="t d"{style_attr}>\n'
        f'<title id="t">{esc(title)}</title><desc id="d">{esc(desc)}</desc>\n'
        f'<style><![CDATA[{css}]]></style>\n{body}\n</svg>\n'
    )


def paper(w: int, h: int, r: int = 18, fill: str | None = None) -> str:
    return f'<rect width="{w}" height="{h}" rx="{r}" fill="{fill or P["paper"]}"/>'


def arrow(x: float, y: float, color: str, cls: str = "", style: str = "") -> str:
    """Small up-right arrow with its box's top-left at (x, y)."""
    attrs = f' class="{cls}"' if cls else ""
    attrs += f' style="{style}"' if style else ""
    return (f'<path{attrs} d="M{x} {y + 14}L{x + 14} {y}M{x + 2} {y}H{x + 14}V{y + 12}" fill="none" stroke="{color}" '
            f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>')


def sample_tag(data: dict) -> str:
    """Small marker so placeholder numbers are never mistaken for real ones."""
    if not data.get("sample"):
        return ""
    return f'<text class="m" x="844" y="22" font-size="10" text-anchor="end" fill="{P["faint"]}">sample data</text>'


# --------------------------------------------------------------------------- data helpers
def load_data() -> dict:
    if not DATA_FILE.exists():
        sys.exit(f"missing {DATA_FILE}; run the Action (or create the file) first")
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def parse_days(data: dict) -> list[tuple[dt.date, int]]:
    days = sorted((dt.date.fromisoformat(d["date"]), int(d["count"])) for d in data["days"])
    if not days:
        sys.exit("data/github.json has no days")
    return days


def fmt_date(d: dt.date) -> str:
    return f"{MON[d.month - 1]} {d.day}, {d.year}"


def compute_stats(days) -> dict:
    total = sum(c for _, c in days)
    active = sum(1 for _, c in days if c > 0)
    best = run = 0
    for _, c in days:
        run = run + 1 if c > 0 else 0
        best = max(best, run)
    i = len(days) - 1
    if days[i][1] == 0:  # today may not have contributions yet; GitHub keeps the streak alive
        i -= 1
    cur = 0
    while i >= 0 and days[i][1] > 0:
        cur += 1
        i -= 1
    busiest = max(days, key=lambda d: (d[1], d[0]))
    return dict(total=total, active=active, span=len(days), current=cur, longest=best, busiest=busiest)


def monthly_totals(days) -> list[tuple[int, int, int]]:
    last = days[-1][0]
    keys, (y, m) = [], (last.year, last.month)
    for _ in range(12):
        keys.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    sums = {k: 0 for k in keys}
    for d, c in days:
        if (d.year, d.month) in sums:
            sums[(d.year, d.month)] += c
    return [(y, m, sums[(y, m)]) for y, m in keys]


# --------------------------------------------------------------------------- hero (ASCII art)
ASCII_RAMP = " .:-=+*#%@"
CW, CH, FS = 4.32, 8.0, 7.2  # character cell and font size for the ASCII art


def portrait_luma(cols: int, rows: int):
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = ASSET_DIR / f"portrait.{ext}"
        if not p.exists():
            continue
        try:
            from PIL import Image, ImageOps
        except ImportError:
            sys.exit("assets/portrait found but Pillow is missing: pip install Pillow")
        im = ImageOps.autocontrast(Image.open(p).convert("L"))
        want = (cols * CW) / (rows * CH)
        w, h = im.size
        if w / h > want:
            nw = int(h * want)
            im = im.crop(((w - nw) // 2, 0, (w + nw) // 2, h))
        else:
            nh = int(w / want)
            im = im.crop((0, (h - nh) // 2, w, (h + nh) // 2))
        im = im.resize((cols, rows))
        px = im.load()
        return [[px[c, r] / 255 for c in range(cols)] for r in range(rows)]
    return None


def ascii_rows(cols: int, rows: int) -> list[tuple[int, str]]:
    """Return (first_column, text) per row. A portrait if present, else a lit sphere."""
    luma = portrait_luma(cols, rows)
    cx, cy, rad = cols * CW / 2, rows * CH / 2, min(cols * CW, rows * CH) * 0.47
    light = (-0.5, -0.6, 0.62)
    norm = math.sqrt(sum(v * v for v in light))
    light = tuple(v / norm for v in light)
    out = []
    for r in range(rows):
        line = ""
        for c in range(cols):
            if luma:
                ink = 1 - luma[r][c]
                ink = 0 if ink < 0.14 else ink
            else:
                dx, dy = ((c + .5) * CW - cx) / rad, ((r + .5) * CH - cy) / rad
                d = math.hypot(dx, dy)
                if d > 1:
                    line += " "
                    continue
                nz = math.sqrt(1 - d * d)
                lum = max(0.0, dx * light[0] + dy * light[1] + nz * light[2])
                lum = 0.1 + 0.62 * lum + ((c * 131 + r * 71) % 7) / 7 * 0.06
                if d > 0.93:  # darker rim keeps the outline readable
                    lum -= (d - 0.93) * 4
                ink = min(1.0, max(0.0, 1 - lum))
            line += ASCII_RAMP[int(ink * (len(ASCII_RAMP) - 1) + 0.5)]
        stripped = line.strip()
        out.append((len(line) - len(line.lstrip()), stripped))
    return out


def card_hero() -> str:
    W, H = 880, 424
    avail = 410
    first, _, rest = NAME.partition(" ")
    rest = rest or first
    size = min(fit_size("d", first, avail, 132, 60), fit_size("di", rest, avail, 132, 60))
    y1 = 66 + size * 0.98
    y2 = y1 + size * 0.84
    lines = wrap("s", HERO_LINE, avail, 16, 2)
    ty = y2 + 42

    site = re.sub(r"^https?://", "", SITE_URL).rstrip("/")
    link = f"github.com/{GITHUB_LOGIN}" if "yourportfolio" in site else site
    link_w = text_width("m", link, 13)

    art_x, art_y, cols, rows = 470, 34, 79, 44
    art = ""
    for r, (c0, text) in enumerate(ascii_rows(cols, rows)):
        if not text:
            continue
        art += (f'<text class="m row" style="--i:{r}" x="{art_x + c0 * CW:.2f}" y="{art_y + r * CH + CH * 0.8:.2f}" '
                f'font-size="{FS}" fill="{P["ink"]}" fill-opacity=".82">{esc(text)}</text>')

    pulse = deco(
        f'<rect x="48" y="52" width="10" height="10" fill="none" stroke="{P["blue"]}" stroke-width="1.5">'
        f'<animate attributeName="opacity" values="1;0;1" dur="2.4s" begin="{T_HERO + 1.2}s" repeatCount="indefinite"/></rect>'
    )
    body = (
        f'<clipPath id="clip"><rect width="{W}" height="{H}" rx="18"/></clipPath>{paper(W, H)}'
        f'<g clip-path="url(#clip)">{art}</g>'
        f'<g class="in" style="--i:0"><rect x="48" y="52" width="10" height="10" fill="{P["blue"]}"/>{pulse}'
        f'<text class="m" x="68" y="61.5" font-size="12" letter-spacing="1.6" fill="{P["mute"]}">{esc(KICKER)}</text></g>'
        f'<text class="d in" style="--i:1" x="44" y="{y1:.1f}" font-size="{size:g}" fill="{P["ink"]}">{esc(first)}</text>'
        f'<text class="di in" style="--i:2" x="44" y="{y2:.1f}" font-size="{size:g}" fill="{P["ink"]}">{esc(rest)}</text>'
        f'<g class="in" style="--i:3">'
        + "".join(f'<text class="s" x="48" y="{ty + i * 23:.1f}" font-size="16" fill="#2A2A30">{esc(l)}</text>' for i, l in enumerate(lines))
        + f'</g><g class="in" style="--i:4"><text class="m" x="48" y="{H - 30}" font-size="13" fill="{P["ink"]}">{esc(link)}</text>'
        f'{arrow(48 + link_w + 10, H - 41, P["blue"])}</g>'
    )
    return svg_doc(W, H, body, NAME, f"{NAME}. {KICKER.title()}. {HERO_LINE}", T_HERO)


# --------------------------------------------------------------------------- stats + graph
def card_graph(data: dict, days, st: dict) -> str:
    W, H = 880, 262
    x0, y0 = 36, 98
    pitch = (W - 72) / 53
    cell = pitch - 3.6
    first_dow = (days[0][0].weekday() + 1) % 7  # Sunday-first rows, like GitHub
    nz = sorted(c for _, c in days if c > 0)
    q = [nz[int(len(nz) * f)] for f in (.25, .5, .75)] if nz else [1, 2, 3]

    def level(c: int) -> int:
        return 0 if c == 0 else 1 if c <= q[0] else 2 if c <= q[1] else 3 if c <= q[2] else 4

    rects, months, last_col, last_m = [], "", -9, None
    ly = y0 + 7 * pitch + 20
    for i, (d, c) in enumerate(days):
        col, row = divmod(i + first_dow, 7)
        rects.append(
            f'<rect class="c" style="--i:{col}" x="{x0 + col * pitch:.1f}" y="{y0 + row * pitch:.1f}" '
            f'width="{cell:.1f}" height="{cell:.1f}" rx="2.6" fill="{LEVELS[level(c)]}"/>'
        )
        if (i == 0 or (d.day <= 7 and row == 0)) and (d.year, d.month) != last_m and col - last_col >= 3:
            months += f'<text class="m" x="{x0 + col * pitch:.1f}" y="{ly}" font-size="11" fill="{P["mute"]}">{MON[d.month - 1]}</text>'
            last_col, last_m = col, (d.year, d.month)
    lx = W - 36 - 5 * (cell + 3) - 74
    gy = 80  # legend sits above the grid, under "REFRESHED DAILY"
    legend = (f'<text class="m" x="{lx}" y="{gy}" font-size="11" fill="{P["mute"]}">less</text>'
              + "".join(f'<rect x="{lx + 40 + k * (cell + 3):.1f}" y="{gy - 10}" width="{cell:.1f}" height="{cell:.1f}" rx="2.6" fill="{LEVELS[k]}"/>' for k in range(5))
              + f'<text class="m" x="{lx + 40 + 5 * (cell + 3) + 4:.1f}" y="{gy}" font-size="11" fill="{P["mute"]}">more</text>')
    body = (
        paper(W, H)
        + f'<text class="d in" style="--i:0" x="36" y="62" font-size="40" fill="{P["ink"]}">Every day, <tspan class="di" fill="{P["blue"]}">in blue</tspan></text>'
        f'<text class="m in" style="--i:0" x="844" y="56" font-size="11" letter-spacing="1.6" text-anchor="end" fill="{P["mute"]}">REFRESHED DAILY</text>'
        f'{"".join(rects)}<g class="in" style="--i:6">{months}{legend}</g>{sample_tag(data)}'
    )
    return svg_doc(W, H, body, "Contribution graph", f'{st["total"]:,} contributions in the past year, shown as a daily calendar.', T_GRAPH)


def card_activity(data: dict, days, st: dict) -> str:
    W, H = 880, 336
    months = monthly_totals(days)
    peak_i = max(range(len(months)), key=lambda i: months[i][2])
    peak = months[peak_i][2] or 1
    base_y, max_h = 292, 118
    pitch, bw, x0 = 42, 30, 36

    bars, labels = [], ""
    for i, (y, m, v) in enumerate(months):
        h = max(4.0, v / peak * max_h)
        x = x0 + i * pitch + (pitch - bw) / 2
        color = P["blue"] if i == peak_i else (P["grey"] if i == len(months) - 1 else P["ink"])
        bars.append(f'<rect class="bar" style="--i:{i}" x="{x:.1f}" y="{base_y - h:.1f}" width="{bw}" height="{h:.1f}" rx="5" fill="{color}"/>')
        labels += (f'<text class="m in" style="--i:{i * 0.3:g}" x="{x + bw / 2:.1f}" y="{base_y + 22}" font-size="11" '
                   f'text-anchor="middle" fill="{P["mute"]}">{MON[m - 1]}</text>')
        if i == peak_i:
            labels += (f'<text class="m lab" x="{x + bw / 2:.1f}" y="{base_y - h - 9:.1f}" font-size="11" text-anchor="middle" '
                       f'fill="{P["blue"]}">{v:,}</text>')

    big_size = fit_size("d", f'{st["total"]:,} contributions', 500, 64, 36)
    b = st["busiest"]
    rx = 600
    cur_word = "day" if st["current"] == 1 else "days"
    rows = [
        (f'{st["current"]}<tspan class="di"> {cur_word}</tspan>', f'current streak, and the longest is {st["longest"]}'),
        (f'{st["active"]}<tspan fill="{P["faint"]}"> / {st["span"]}</tspan>', "days with a commit"),
        (f'{b[1]}', f'on the busiest day, {fmt_date(b[0])}'),
    ]
    right = ""
    for i, (val, sub) in enumerate(rows):
        y = 112 + i * 90
        if i:
            right += f'<path d="M{rx} {y - 50}H844" stroke="{P["line"]}" stroke-width="1.2"/>'
        right += (f'<g class="in" style="--i:{i + 1}"><text class="d" x="{rx}" y="{y}" font-size="44" fill="{P["ink"]}">{val}</text>'
                  f'<text class="s" x="{rx}" y="{y + 24}" font-size="13" fill="{P["mute"]}">{esc(sub)}</text></g>')
    body = (
        paper(W, H)
        + f'<text class="m in" style="--i:0" x="36" y="50" font-size="11" letter-spacing="1.6" fill="{P["mute"]}">THE LAST 12 MONTHS ON GITHUB</text>'
        f'<text class="d in" style="--i:1" x="36" y="116" font-size="{big_size:g}" fill="{P["ink"]}">{st["total"]:,}<tspan class="di"> contributions</tspan></text>'
        f'{"".join(bars)}{labels}{right}{sample_tag(data)}'
    )
    desc = (f'{st["total"]:,} contributions in the last 12 months. Current streak {st["current"]} days, '
            f'{st["active"]} of {st["span"]} days with a commit, busiest day {fmt_date(b[0])} with {b[1]}.')
    return svg_doc(W, H, body, "Contributions per month", desc, T_STATS)


# --------------------------------------------------------------------------- tiles, heading, repos
def card_tile(spec, idx: int) -> str:
    _, kicker, roman, ital, blurb, bg, fg, _ = spec
    W, H = 290, 212
    base = T_TILES + idx * 0.15
    big = fit_size("d", roman + ital, 246, 42, 28)
    lines = wrap("s", blurb, 246, 13, 3)
    sweep = deco(
        f'<rect x="-90" y="-10" width="70" height="{H + 20}" fill="#fff" fill-opacity=".16" transform="skewX(-20)">'
        f'<animateTransform attributeName="transform" type="translate" additive="sum" values="0 0;520 0;520 0" keyTimes="0;.3;1" '
        f'dur="6s" begin="{base + 1.2}s" repeatCount="indefinite"/></rect>'
    )
    body = (
        f'<clipPath id="clip"><rect width="{W}" height="{H}" rx="14"/></clipPath>'
        f'<g clip-path="url(#clip)"><rect width="{W}" height="{H}" fill="{P[bg]}"/>{sweep}</g>'
        f'<text class="m in" style="--i:0" x="22" y="38" font-size="11" letter-spacing="1.6" fill="{fg}">{kicker}</text>'
        f'{arrow(254, 24, fg, "in", "--i:0")}'
        f'<text class="d in" style="--i:1" x="22" y="112" font-size="{big:g}" fill="{fg}">{esc(roman)}<tspan class="di">{esc(ital)}</tspan></text>'
        f'<g class="in" style="--i:2">'
        + "".join(f'<text class="s" x="22" y="{146 + i * 19}" font-size="13" fill="{fg}" fill-opacity=".92">{esc(l)}</text>' for i, l in enumerate(lines))
        + "</g>"
    )
    return svg_doc(W, H, body, kicker.title(), f"{kicker.title()}: {roman}{ital}. {blurb}", base)


def card_heading() -> str:
    W, H = 880, 84
    body = (
        paper(W, H)
        + f'<text class="d in" style="--i:0" x="36" y="56" font-size="42" fill="{P["ink"]}">What I\'m <tspan class="di">coding</tspan></text>'
    )
    return svg_doc(W, H, body, "What I'm coding", "Featured repositories", T_HEAD)


def _star(x: float, y: float) -> str:
    return (f'<path transform="translate({x:.1f} {y:.1f}) scale(.8)" d="M0-8l2.3 4.9 5.3.7-3.9 3.7 1 5.3L0 3.9l-4.7 2.7 1-5.3-3.9-3.7 5.3-.7z" '
            f'fill="{P["mute"]}"/>')


def card_repo(repo: dict, idx: int) -> str:
    W, H = 290, 200
    base = T_REPOS + idx * 0.15
    name = repo["name"]
    desc = (repo.get("description") or "").strip() or "No description yet."
    lines = wrap("s", desc, 246, 13, 3)
    palette = [P["blue"], P["red"], P["yellow"]]
    dot = repo.get("languageColor") or palette[idx % 3]
    lang = repo.get("language")
    stars, forks = repo.get("stars", 0), repo.get("forks", 0)

    x, fy = 22, 176
    foot = ""
    if lang:
        foot += f'<text class="m" x="{x}" y="{fy}" font-size="11" fill="{P["mute"]}">{esc(lang)}</text>'
        x += text_width("m", lang, 11) + 8
        foot += f'<text class="m" x="{x}" y="{fy}" font-size="11" fill="{P["faint"]}">·</text>'
        x += 14
    foot += f'{_star(x + 4, fy - 4)}<text class="m" x="{x + 13}" y="{fy}" font-size="11" fill="{P["mute"]}">{stars:,}</text>'
    x += 13 + text_width("m", f"{stars:,}", 11) + 12
    if forks:
        foot += f'<text class="m" x="{x}" y="{fy}" font-size="11" fill="{P["mute"]}">forks {forks:,}</text>'
    body = (
        f'<clipPath id="clip"><rect width="{W}" height="{H}" rx="14"/></clipPath>'
        f'<rect width="{W}" height="{H}" rx="14" fill="{P["white"]}"/>'
        f'<g class="in" style="--i:0"><rect x="22" y="26" width="11" height="11" rx="2" fill="{esc(dot)}"/>'
        f'<text class="sb" x="42" y="37" font-size="17" fill="{P["ink"]}">{esc(ellipsize("sb", name, 226, 17))}</text></g>'
        f'<g class="in" style="--i:1">'
        + "".join(f'<text class="s" x="22" y="{72 + i * 19}" font-size="13" fill="{P["mute"]}">{esc(l)}</text>' for i, l in enumerate(lines))
        + f'</g><g class="in" style="--i:2">{foot}</g>'
    )
    return svg_doc(W, H, body, f"Repository {name}", f"{name}: {desc}", base)


# --------------------------------------------------------------------------- README + preview
def stack_markup(prefix: str, repos: list[dict]) -> dict[str, str]:
    tiles = "\n".join(
        f'<a href="{esc(t[7])}"><img src="{prefix}{t[0]}" width="32.5%" alt="{esc(t[1].title())}: {esc(t[2] + t[3])}"></a>'
        for t in TILES
    )
    repo_html = "\n".join(
        f'<a href="{esc(r.get("url") or "#")}"><img src="{prefix}repo-{i + 1}.svg" width="32.5%" alt="{esc(r["name"])}"></a>'
        for i, r in enumerate(repos)
    )
    links = " / ".join(f'<a href="{esc(u)}">{n}</a>' for n, u in (
        ("portfolio", SITE_URL), ("linkedin", LINKEDIN_URL), ("email", f"mailto:{EMAIL}"), ("github", f"https://github.com/{GITHUB_LOGIN}")))
    return dict(
        hero=f'<img src="{prefix}hero.svg" width="100%" alt="{esc(NAME)}: {esc(HERO_LINE)}">',
        graph=f'<img src="{prefix}graph.svg" width="100%" alt="Contribution graph for the past year">',
        activity=f'<img src="{prefix}activity.svg" width="100%" alt="Contributions in the last 12 months, current streak, days with a commit and busiest day">',
        tiles=tiles,
        heading=f'<img src="{prefix}heading-repos.svg" width="100%" alt="What I\'m coding">',
        repos=repo_html,
        links=links,
    )


def write_preview(repos: list[dict]) -> None:
    m = stack_markup("", repos)
    order = ("hero", "graph", "activity", "tiles", "heading", "repos")
    parts = "\n".join(m[k] for k in order if m[k]) + f'\n<p class="links">{m["links"]}</p>'
    page = (
        '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>README preview (static)</title>"
        "<style>body{margin:0;background:#0d1117;color:#e6edf3;font:14px system-ui}main{max-width:838px;margin:0 auto;padding:24px;text-align:center}"
        "img{vertical-align:middle}a{color:#4493f8}.links{margin:10px 0}</style>"
        f"<main>\n{parts}\n</main>\n"
    )
    (OUT_DIR / "preview.html").write_text(page, encoding="utf-8")


def update_readme(repos: list[dict]) -> None:
    readme = ROOT / "README.md"
    if not readme.exists():
        return
    text = readme.read_text(encoding="utf-8")
    pat = re.compile(r"(<!--REPOS:START-->\n).*?(\n?<!--REPOS:END-->)", re.S)
    if not pat.search(text):
        print("README.md has no <!--REPOS:START--> / <!--REPOS:END--> markers; left untouched")
        return
    block = stack_markup("cards/", repos)["repos"]
    new = pat.sub(lambda mm: mm.group(1) + block + mm.group(2), text)
    if new != text:
        readme.write_text(new, encoding="utf-8")


# --------------------------------------------------------------------------- main
def main() -> None:
    data = load_data()
    days = parse_days(data)
    st = compute_stats(days)
    repos = (data.get("repos") or [])[:MAX_REPOS]

    OUT_DIR.mkdir(exist_ok=True)
    for pattern in ("repo-*.svg", "tile-*.svg"):
        for old in OUT_DIR.glob(pattern):
            old.unlink()

    cards = {
        "hero.svg": card_hero(),
        "graph.svg": card_graph(data, days, st),
        "activity.svg": card_activity(data, days, st),
        "heading-repos.svg": card_heading(),
    }
    for i, spec in enumerate(TILES):
        cards[spec[0]] = card_tile(spec, i)
    for i, repo in enumerate(repos):
        cards[f"repo-{i + 1}.svg"] = card_repo(repo, i)

    for name, svg in cards.items():
        (OUT_DIR / name).write_text(svg, encoding="utf-8")
        print(f"{OUT_DIR.name}/{name:<20} {len(svg) / 1024:6.1f} kB")

    if STATIC:
        write_preview(repos)
        print(f"static final frames written; open {OUT_DIR.name}/preview.html and screenshot it")
    else:
        update_readme(repos)


if __name__ == "__main__":
    main()
