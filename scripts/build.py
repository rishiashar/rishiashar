#!/usr/bin/env python3
"""
Builds assets/hero-light.svg and assets/hero-dark.svg for the profile README.

    pip install fonttools brotli pillow
    python3 scripts/build.py

The hero is a screen recording of Figma, drawn by hand. Rishi draws a frame,
drops in the sky from rishiashar.com and starts typing the headline; Claude
finishes the sentence and adds the nav; Rishi tunes the easing in the
Prototype tab. GitHub picks the light or dark file to match the viewer's theme,
and the frame shows rishiashar.com in that same theme.

All motion is CSS keyframes, so it runs inside GitHub's image sandbox and
collapses to the finished screen under prefers-reduced-motion. Fonts come from
npm and the hero art from rishiashar.com; both are cached in scripts/.cache.
"""
from __future__ import annotations

import base64
import io
import math
import random
import tarfile
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
CACHE = ROOT / "scripts" / ".cache"
SITE = "https://rishiashar.com"


def fetch(name: str, url: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / name
    if not path.exists():
        print(f"fetching {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "image/webp,image/png,*/*"})
        with urllib.request.urlopen(req) as r:
            path.write_bytes(r.read())
    return path.read_bytes()


# ------------------------------------------------------------------ fonts ---
PACKAGES = {
    "inter": "https://registry.npmjs.org/@fontsource-variable/inter/-/inter-5.3.0.tgz",
    "fraunces": "https://registry.npmjs.org/@fontsource-variable/fraunces/-/fraunces-5.3.0.tgz",
    "manrope": "https://registry.npmjs.org/@fontsource-variable/manrope/-/manrope-5.3.0.tgz",
}
FONT_FILES = {
    # key: (package, file, variable-font instance, css family, css weight)
    "ui": ("inter", "inter-latin-wght-normal.woff2", {"wght": 450}, "IN", 450),        # Figma UI3
    "ui-md": ("inter", "inter-latin-wght-normal.woff2", {"wght": 550}, "IN", 550),
    "ui-sb": ("inter", "inter-latin-wght-normal.woff2", {"wght": 600}, "IN", 600),
    "serif": ("fraunces", "fraunces-latin-full-normal.woff2",                         # rishiashar.com display
              {"opsz": 72, "wght": 400, "SOFT": 0, "WONK": 0}, "FR", 400),   # opsz 72 matches the live site to ~2px
    "site": ("manrope", "manrope-latin-wght-normal.woff2", {"wght": 400}, "MR", 400),  # rishiashar.com body
}
FALLBACK = {
    "IN": "Inter,ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif",
    "FR": "Georgia,'Times New Roman',serif",
    "MR": "ui-sans-serif,system-ui,sans-serif",
}


class Font:
    def __init__(self, key: str):
        pkg, file, inst, self.family, self.weight = FONT_FILES[key]
        tgz = fetch(f"{pkg}.tgz", PACKAGES[pkg])
        raw = tarfile.open(fileobj=io.BytesIO(tgz)).extractfile(f"package/files/{file}").read()
        font = instancer.instantiateVariableFont(TTFont(io.BytesIO(raw)), inst)
        font.flavor = None
        buf = io.BytesIO()
        font.save(buf)
        self.data = buf.getvalue()
        self.cmap = font.getBestCmap()
        self.hmtx = font["hmtx"]
        self.upm = font["head"].unitsPerEm

    def width(self, s: str, size: float, tracking: float = 0) -> float:
        return sum(self.hmtx[self.cmap[ord(c)]][0] for c in s) * size / self.upm + tracking * len(s)

    def woff2(self, chars: str) -> bytes:
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["kern", "tnum"]
        opts.hinting = False
        opts.desubroutinize = True
        opts.name_IDs = [1, 2]
        font = TTFont(io.BytesIO(self.data))
        sub = subset.Subsetter(opts)
        sub.populate(text=chars)
        sub.subset(font)
        out = io.BytesIO()
        font.save(out)
        return out.getvalue()


FONTS = {k: Font(k) for k in FONT_FILES}


# ------------------------------------------------------------ svg builder ---
def f(n: float) -> str:
    s = f"{n:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def complement(windows):
    """Times not covered by windows [(start, end|None)], same format."""
    out, start = [], 0.0
    for a, b in windows:
        if a > start:
            out.append((start, a))
        start = b
        if start is None:
            return out
    return out + [(start, None)]


class Svg:
    def __init__(self, w, h, title, label):
        self.w, self.h, self.title, self.label = w, h, title, label
        self.body, self.css, self.defs = [], [], []
        self.chars: dict[str, set[str]] = {}
        self.n = 0

    def add(self, *parts: str) -> None:
        self.body.extend(parts)

    def text(self, x, y, s, font="ui", size=11, fill="#000", anchor="start", tracking=0, num=False) -> str:
        self.chars.setdefault(font, set()).update(s)
        fo = FONTS[font]
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        t = f' letter-spacing="{f(tracking)}"' if tracking else ""
        n = ' style="font-variant-numeric:tabular-nums"' if num else ""
        return (f'<text x="{f(x)}" y="{f(y)}" class="{fo.family.lower()}" font-weight="{fo.weight}" '
                f'font-size="{f(size)}" fill="{fill}"{a}{t}{n}>{escape(s)}</text>')

    def anim(self, frames, *, iterations="1", delay=0.0) -> str:
        """frames: [(seconds, 'css', easing|None)] in time order -> class name."""
        clean = []
        for t, decl, ease in frames:
            t = round(t, 3)
            assert not clean or t >= clean[-1][0], f"keyframes out of order at {t}"
            if clean and t == clean[-1][0]:
                clean[-1] = (t, decl, ease)
            else:
                clean.append((t, decl, ease))
        if len(clean) == 1:
            clean.append((clean[0][0] + .001, clean[0][1], None))
        total = clean[-1][0]
        self.n += 1
        name = f"a{self.n}"
        pct = lambda t: f"{t / total * 100:.4f}".rstrip("0").rstrip(".")     # ms-exact over a 10s+ timeline
        body = "".join(f"{pct(t)}%{{{d}{';animation-timing-function:' + e if e else ''}}}" for t, d, e in clean)
        self.css.append(f"@keyframes {name}{{{body}}}")
        self.css.append(f".{name}{{animation:{name} {total:g}s linear {round(delay, 3):g}s {iterations} both}}")
        return name

    def show(self, windows) -> str:
        """Attributes that switch an element on and off instantly, like real UI."""
        if windows == [(0, None)]:
            return ""
        fr = [(0, f"opacity:{1 if windows[0][0] == 0 else 0}", "steps(1,end)")]
        for a, b in windows:
            if a > 0:
                fr.append((a, "opacity:1", "steps(1,end)"))
            if b is not None:
                fr.append((b, "opacity:0", "steps(1,end)"))
        return f'class="{self.anim(fr)}"' + ("" if windows[-1][1] is None else ' style="opacity:0"')

    def states(self, items) -> str:
        """items: [(start, svg)]; each shows until the next one starts and the last one stays."""
        out = []
        for i, (t, body) in enumerate(items):
            end = items[i + 1][0] if i + 1 < len(items) else None
            if end is not None and end <= t:
                continue
            out.append(f"<g {self.show([(t, end)])}>{body}</g>")
        return "".join(out)

    def render(self) -> str:
        faces, used = [], set()
        for key, chars in sorted(self.chars.items()):
            fo = FONTS[key]
            used.add(fo.family)
            data = base64.b64encode(fo.woff2("".join(sorted(chars)) + " ")).decode()
            faces.append(f"@font-face{{font-family:{fo.family};font-weight:{fo.weight};"
                         f"src:url(data:font/woff2;base64,{data}) format('woff2')}}")
        fams = "".join(f".{k.lower()}{{font-family:{k},{v}}}" for k, v in FALLBACK.items() if k in used)
        css = ("".join(faces) + fams + "".join(self.css)
               + "@media (prefers-reduced-motion:reduce){*{animation:none!important}}")
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{escape(self.label)}" fill="none">'
                f"<title>{escape(self.title)}</title><defs>{''.join(self.defs)}</defs>"
                f"<style>{css}</style>{''.join(self.body)}</svg>")


# ----------------------------------------------------------------- images ---
def site_image(name: str, path: str) -> Image.Image:
    return Image.open(io.BytesIO(fetch(name, SITE + path))).convert("RGBA")


def data_uri(im: Image.Image, fmt="JPEG", q=80) -> str:
    buf = io.BytesIO()
    if fmt == "JPEG":
        im.convert("RGB").save(buf, "JPEG", quality=q, optimize=True, progressive=True)
    else:
        im.save(buf, "PNG", optimize=True)
    return f"data:image/{fmt.lower()};base64,{base64.b64encode(buf.getvalue()).decode()}"


def cover(im: Image.Image, w: int, h: int) -> Image.Image:
    s = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


# ----------------------------------------------------------------- motion ---
def minjerk(u: float) -> float:
    """Minimum-jerk profile: how a hand actually moves a mouse."""
    return u * u * u * (10 - 15 * u + 6 * u * u)


class Track:
    """A cursor's path: holds, curved human moves that overshoot a touch, and presses."""

    def __init__(self, start, rng):
        self.pts = [(0.0, start)]
        self.presses: list[tuple[float, float]] = []
        self.rng = rng

    @property
    def pos(self):
        return self.pts[-1][1]

    def to(self, p1, t0, t1, arc=.1, over=.025):
        """Move to p1 between t0 and t1; returns the samples so other things can follow the hand."""
        assert t0 >= self.pts[-1][0] - 1e-9, f"move at {t0} starts before {self.pts[-1][0]}"
        if t0 > self.pts[-1][0]:
            self.pts.append((t0, self.pos))
        p0 = self.pos
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        d = math.hypot(dx, dy) or 1
        ux, uy = dx / d, dy / d
        k = self.rng.uniform(.35, 1) * arc * self.rng.choice((-1, 1))
        ctrl = (p0[0] + dx / 2 - uy * d * k, p0[1] + dy / 2 + ux * d * k)
        o = min(d * over, 6)
        po = (p1[0] + ux * o, p1[1] + uy * o)
        main = .84 if o > .5 else 1
        n = max(8, int((t1 - t0) * 30))       # 30 samples/s: smooth on screen, light on bytes
        out = []
        for i in range(1, n + 1):
            u = i / n
            if u <= main:
                s = minjerk(u / main)
                x = (1 - s) ** 2 * p0[0] + 2 * (1 - s) * s * ctrl[0] + s * s * po[0]
                y = (1 - s) ** 2 * p0[1] + 2 * (1 - s) * s * ctrl[1] + s * s * po[1]
            else:
                s = 1 - (1 - (u - main) / (1 - main)) ** 3
                x, y = po[0] + (p1[0] - po[0]) * s, po[1] + (p1[1] - po[1]) * s
            out.append((t0 + (t1 - t0) * u, (x, y)))
        self.pts.extend(out)
        return out

    def press(self, t, hold=.09):
        self.presses.append((t, t + hold))

    def css(self, svg: Svg) -> tuple[str, str]:
        move = svg.anim([(t, f"transform:translate({f(x)}px,{f(y)}px)", None) for t, (x, y) in self.pts])
        fr = [(0, "transform:scale(1)", None)]
        for a, b in sorted(self.presses):
            fr += [(a - .001, "transform:scale(1)", "ease-out"), (a + .06, "transform:scale(.88)", None),
                   (b, "transform:scale(.88)", "ease-out"), (b + .14, "transform:scale(1)", None)]
        fr.append((self.pts[-1][0] + .5, "transform:scale(1)", None))
        return move, svg.anim(fr)


def idle_loop(svg: Svg, rng, start, period, moves):
    """Occasional small repositioning while someone reads. Not a floaty sine wave."""
    fr, pos = [(0, "transform:translate(0,0)", None)], (0.0, 0.0)
    for at, (x, y) in moves:
        fr.append((at, f"transform:translate({f(pos[0])}px,{f(pos[1])}px)", None))
        dur = rng.uniform(.45, .7)
        for i in range(1, 9):
            s = minjerk(i / 8)
            fr.append((at + dur * i / 8, f"transform:translate({f(pos[0] + (x - pos[0]) * s)}px,{f(pos[1] + (y - pos[1]) * s)}px)", None))
        pos = (x, y)
    fr.append((period, f"transform:translate({f(pos[0])}px,{f(pos[1])}px)", None))
    return svg.anim(fr, iterations="infinite", delay=start)


# ----------------------------------------------------------------- themes ---
FIGMA = {   # Figma UI3
    "dark": dict(tabbar="#1F1F1F", panel="#2C2C2C", canvas="#1E1E1E", border="#444444", div="#383838",
                 field="#383838", t1="#FFFFFF", t2="#C4C4C4", t3="#8F8F8F", row="#2D4A6B", icon="#FFFFFF",
                 editor="#1E1E1E", grid="#2A2A2A", curve="#FFFFFF", instance="#C58AFF", window="#4D4D4D",
                 shadow=".55", canvas_hex="1E1E1E"),
    "light": dict(tabbar="#ECECEC", panel="#FFFFFF", canvas="#E5E5E5", border="#E6E6E6", div="#E6E6E6",
                  field="#F5F5F5", t1="#1E1E1E", t2="#5C5C5C", t3="#8F8F8F", row="#E5F4FF", icon="#1E1E1E",
                  editor="#F5F5F5", grid="#E9E9E9", curve="#1E1E1E", instance="#8638E5", window="#D4D4D4",
                  shadow=".16", canvas_hex="E5E5E5"),
}
SITE_THEME = {  # rishiashar.com
    "light": dict(bg="#F5F5F5", nav="#FFFFFF", nav_line="#E6E6E6", name="#111111", link="#555555",
                  btn="#FFFFFF", btn_line="#E2E2E2", btn_icon="#3A3A3A", ink="#111111",
                  card="#FFFFFF", card_line="#E6E6E6", sky="hero-bg-light-v2", scrim=("#FFFFFF", .28, .08, .36)),
    "dark": dict(bg="#0B0D10", nav="#0B0D10", nav_line="#1D2127", name="#EDEDED", link="#9DA3AB",
                 btn="#14171B", btn_line="#262A31", btn_icon="#C9CDD2", ink="#F2F2F2",
                 card="#1A1D22", card_line="#2A2F36", sky="hero-bg-dark-v2", scrim=("#000000", .22, .05, .2)),
}
BLUE = "#0D99FF"
RISHI = "#14AE5C"     # one of Figma's multiplayer colours
CLAUDE = "#D97757"    # Claude's coral
OUT_EXPO = "cubic-bezier(.16,1,.3,1)"

CURSOR = "M0 0 L0 15.5 L4.3 11.6 L7.2 18.2 L9.9 17 L7.1 10.6 L12.8 10.6 Z"
HEADLINE = "I am an Experience Designer in Toronto {cn}, designing and prototyping ideas using |AI {spark} and {tools} emerging tools."  # | = line break
RISHI_TYPES = "I am an Experience Designer"
CHAT = "matched it to your site"


# ------------------------------------------------------------------ icons ---
def chev(x, y, c, s=3):
    return (f'<path d="M{f(x - s)} {f(y - s / 2)} L{f(x)} {f(y + s / 2)} L{f(x + s)} {f(y - s / 2)}" stroke="{c}" '
            f'stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>')


def plus(x, y, c):
    return f'<path d="M{f(x - 5)} {f(y)} H{f(x + 5)} M{f(x)} {f(y - 5)} V{f(y + 5)}" stroke="{c}" stroke-width="1.1" stroke-linecap="round"/>'


def eye(x, y, c):
    return (f'<path d="M{x - 6} {y} Q{x} {y - 6} {x + 6} {y} Q{x} {y + 6} {x - 6} {y}Z" stroke="{c}" stroke-width="1.1"/>'
            f'<circle cx="{x}" cy="{y}" r="1.8" fill="{c}"/>')


def figma_mark(x, y, s, c):
    return (f'<g transform="translate({f(x)} {f(y)}) scale({s})" fill="{c}"><path d="M6 0H3A3 3 0 0 0 3 6H6Z"/>'
            '<path d="M6 0H9A3 3 0 0 1 9 6H6Z"/><path d="M6 6H3A3 3 0 0 0 3 12H6Z"/><circle cx="9" cy="9" r="3"/>'
            '<path d="M3 12H6V15A3 3 0 1 1 3 12Z"/></g>')


def claude_burst(r, c="#fff"):
    rays = "".join(f'<line x1="{f(math.cos(a) * r * .3)}" y1="{f(math.sin(a) * r * .3)}" x2="{f(math.cos(a) * r)}" '
                   f'y2="{f(math.sin(a) * r)}"/>' for a in (i * math.pi / 4 + math.pi / 8 for i in range(8)))
    return f'<g stroke="{c}" stroke-width="1.6" stroke-linecap="round">{rays}</g>'


TOOL_ICONS = {
    "move": lambda c: f'<path d="M-4 -6.5 L-4 5.5 L-1 2.6 L1.3 7.3 L3.1 6.5 L.9 1.9 L4.9 1.9 Z" fill="{c}" stroke="{c}" stroke-width=".5" stroke-linejoin="round"/>',
    "frame": lambda c: f'<path d="M-2.5 -7 V7 M2.5 -7 V7 M-7 -2.5 H7 M-7 2.5 H7" stroke="{c}" stroke-width="1.2" stroke-linecap="round"/>',
    "rect": lambda c: f'<rect x="-6" y="-6" width="12" height="12" rx="1" stroke="{c}" stroke-width="1.2"/>',
    "pen": lambda c: f'<path d="M0 -7 L5 1 L2.5 6.5 H-2.5 L-5 1 Z M0 -7 V-.8" stroke="{c}" stroke-width="1.15" stroke-linejoin="round"/><circle cx="0" cy=".6" r="1.3" stroke="{c}" stroke-width="1.1"/>',
    "text": lambda c: f'<path d="M-5.5 -6 H5.5 M0 -6 V7" stroke="{c}" stroke-width="1.4" stroke-linecap="round"/>',
    "comment": lambda c: f'<path d="M-6.3 6.3 L-5.4 2.6 A6.5 6.5 0 1 1 -2.6 5.4 Z" stroke="{c}" stroke-width="1.2" stroke-linejoin="round"/>',
}
LAYER_ICONS = {
    "frame": lambda c: f'<path d="M-2.4 -5 V5 M2.4 -5 V5 M-5 -2.4 H5 M-5 2.4 H5" stroke="{c}" stroke-linecap="round"/>',
    "text": lambda c: f'<path d="M-4 -4.5 H4 M0 -4.5 V5" stroke="{c}" stroke-width="1.15" stroke-linecap="round"/>',
    "image": lambda c: f'<rect x="-5" y="-4.5" width="10" height="9" rx="1.5" stroke="{c}"/><path d="M-5 3 L-1.5 -.5 L1.5 2 L3 .8 L5 2.6" stroke="{c}" stroke-linejoin="round"/>',
    "instance": lambda c: f'<path d="M0 -5 L5 0 L0 5 L-5 0Z" stroke="{c}" stroke-linejoin="round"/>',
    "auto": lambda c: f'<rect x="-5" y="-5" width="10" height="10" rx="1.5" stroke="{c}"/><path d="M-2.5 -2 H2.5 M-2.5 0 H2.5 M-2.5 2 H2.5" stroke="{c}" stroke-linecap="round"/>',
}


# --------------------------------------------------------------- headline ---
# Site-px geometry of rishiashar.com's hero, measured from the live page at 1280px wide.
H_SIZE, H_TRACK, H_LEAD = 38, -.57, 44.5
H_BOX = (122.5, 654.5)   # the site's 527px h1, plus a little slack for kerning we don't measure
H_TOP, H_BASE = 218, 255
ICONS = {"cn": (52, -32), "spark": (38, -31), "tools": (118, -46)}   # token width, top offset from baseline


def words_of(markup: str):
    out = []
    for w in markup.split(" "):
        parts, buf, i = [], "", 0
        while i < len(w):
            if w[i] == "{":
                j = w.index("}", i)
                if buf:
                    parts.append(("t", buf))
                    buf = ""
                parts.append(("i", w[i + 1:j]))
                i = j + 1
            else:
                buf += w[i]
                i += 1
        if buf:
            parts.append(("t", buf))
        out.append(parts)
    return out


def part_w(p):
    return FONTS["serif"].width(p[1], H_SIZE, H_TRACK) if p[0] == "t" else ICONS[p[1]][0]


def layout(markup: str):
    """Greedy wrap inside the site's h1 box, every line centred. -> [(baseline, [(x, part)], end_x)]"""
    space = FONTS["serif"].width(" ", H_SIZE, H_TRACK)
    lines, cur, cur_w = [], [], 0.0
    for raw in markup.rstrip(" ").split(" "):
        word = words_of(raw.lstrip("|"))[0]
        ww = sum(part_w(p) for p in word)
        add = ww + (space if cur else 0)
        if cur and (raw.startswith("|") or cur_w + add > H_BOX[1] - H_BOX[0]):
            lines.append((cur, cur_w))
            cur, cur_w = [word], ww
        else:
            cur.append(word)
            cur_w += add
    trailing = space if markup.endswith(" ") else 0
    lines.append((cur, cur_w + trailing))
    cx = (H_BOX[0] + H_BOX[1]) / 2
    placed, base = [], H_BASE
    for ws, lw in lines:
        if any(p == ("i", "tools") for w in ws for p in w):
            base += 9
        x = cx - lw / 2
        row = []
        for j, word in enumerate(ws):
            if j:
                x += space
            for p in word:
                row.append((x, p))
                x += part_w(p)
        placed.append((base, row, x + (trailing if ws is lines[-1][0] else 0)))
        base += H_LEAD
    return placed


def render_headline(svg: Svg, markup: str, ink: str) -> list[str]:
    """One SVG string per line."""
    space = FONTS["serif"].width(" ", H_SIZE, H_TRACK)
    lines = []
    for base, row, _ in layout(markup):
        out = []
        run, run_x = "", None
        for x, p in row + [(None, ("end", ""))]:
            if p[0] == "t":
                if run_x is None:
                    run_x, run = x, p[1]
                else:
                    gap = x - (run_x + FONTS["serif"].width(run, H_SIZE, H_TRACK))
                    run += " " * max(0, round(gap / space)) + p[1]
                continue
            if run:
                out.append(svg.text(run_x, base, run, "serif", H_SIZE, ink, tracking=H_TRACK))
                run, run_x = "", None
            if p[0] == "i":
                out.append(f'<use href="#{p[1]}" transform="translate({f(x)} {f(base + ICONS[p[1]][1])})"/>')
        lines.append("".join(out))
    return lines


# ===================================================================== HERO ===
def hero(mode: str) -> None:
    ui, site = FIGMA[mode], SITE_THEME[mode]
    rng = random.Random(7)
    W, H = 846, 500
    TB, LP, RP = 36, 192, 614
    s = Svg(W, H, "Rishi Ashar",
            "A Figma file. Rishi draws a frame, drops in the sky from rishiashar.com and types 'I am an Experience "
            "Designer'. Claude joins and finishes the sentence: in Toronto, designing and prototyping ideas using AI "
            "and emerging tools. Then Rishi opens the Prototype tab and drags a custom bezier into ease-out-expo.")

    # ---- geometry ------------------------------------------------------------------------
    Z = .46                                   # canvas zoom
    fw, fh = 780 * Z, 684 * Z                 # the site's 780 x 684 hero
    fx, fy = (LP + RP) / 2 - fw / 2, 66

    def S(x, y):
        return (fx + x * Z, fy + y * Z)

    px0, px1 = RP + 12, W - 12
    tabs_y = 104
    wd, wp = FONTS["ui-sb"].width("Design", 11), FONTS["ui-sb"].width("Prototype", 11)
    ex0, ey0, ew, eh = px0, 298, px1 - px0, 104          # bezier editor
    q0, q3 = (ex0 + 18, ey0 + eh - 16), (ex0 + ew - 18, ey0 + 16)
    ddx, ddy = q3[0] - q0[0], q3[1] - q0[1]

    def bz(a, b):
        return (q0[0] + a * ddx, q0[1] + b * ddy)

    p1a, p1b, p2 = bz(.42, 0), bz(.16, 1), bz(.3, 1)

    # ---- timeline (seconds) -----------------------------------------------------------------
    T = dict(frame_key=.45, drag0=1.05, drag1=1.85, out0=2.05, out1=2.3, in0=2.45, in1=3.1, drop=3.25,
             text_key=3.45, box0=3.95, box1=4.35, type0=4.5)
    t, char_t = T["type0"], []
    for ch in RISHI_TYPES:   # quick inside words, a beat at spaces and capitals
        char_t.append(t)
        t += rng.uniform(.045, .085) + (.07 if ch == " " else 0) + (.04 if ch.isupper() else 0)
    T["typed"] = t
    T["esc"] = t + .25
    T["claude_in"] = t - .5
    T["stream0"] = t + .35
    chunks = [" " + w for w in HEADLINE[len(RISHI_TYPES):].split(" ")[1:]]   # Claude streams tokens, not keys
    t, stream_t = T["stream0"], []
    for _ in chunks:
        stream_t.append(t)
        t += rng.uniform(.05, .085)
    T["streamed"] = t
    T["nav"] = t + .4
    T["cur"] = t + .85
    T["chat"] = t + 1.1
    T["hover_frame"] = T["cur"] + .15
    T["sel_frame"] = T["hover_frame"] + .15
    T["tab_hover"] = T["sel_frame"] + .45
    T["proto"] = T["tab_hover"] + .12
    T["bz0"] = T["proto"] + .45
    T["bz1"] = T["bz0"] + .7
    T["end"] = T["bz1"] + .6
    T["chat_end"] = T["chat"] + 3.6

    # ---- the two hands, in time order -----------------------------------------------------------
    rishi = Track((fx + fw * .72, fy + fh * .72), rng)
    rishi.to((fx, fy), .5, T["drag0"] - .05)
    rishi.press(T["drag0"] - .02, T["drag1"] - T["drag0"] + .02)
    drag = rishi.to((fx + fw, fy + fh), T["drag0"], T["drag1"], arc=.05, over=.035)
    rishi.to((W + 30, fy + fh * .35), T["out0"], T["out1"], arc=.05)            # off to Finder
    rishi.press(T["in0"], T["drop"] - T["in0"])                                  # back, holding the file
    rishi.to((fx + fw * .58, fy + fh * .52), T["in0"], T["in1"], arc=.08)
    bx0, by0 = S(H_BOX[0] - 4, H_TOP - 2)
    bx1, by1 = S(H_BOX[1] + 4, H_TOP + 46)
    rishi.to((bx0, by0), T["text_key"] + .05, T["box0"] - .05)
    rishi.press(T["box0"] - .02, T["box1"] - T["box0"] + .02)
    bdrag = rishi.to((bx1, by1), T["box0"], T["box1"], arc=.04, over=.02)
    rishi.to(S(715, 168), T["typed"] + .05, T["typed"] + .6, arc=.12)
    rishi.to((fx + 14, fy - 10), T["hover_frame"] - .45, T["hover_frame"])
    rishi.press(T["sel_frame"] - .03)
    rishi.to((px0 + wd + 26 + wp / 2, tabs_y - 5), T["sel_frame"] + .08, T["tab_hover"])
    rishi.press(T["proto"] - .03)
    rishi.to(p1a, T["proto"] + .08, T["bz0"] - .04)
    rishi.press(T["bz0"] - .03, T["bz1"] - T["bz0"] + .03)
    bdr = rishi.to(p1b, T["bz0"], T["bz1"], arc=.08, over=.06)
    rishi.to((p1b[0] + 30, p1b[1] + 38), T["bz1"] + .12, T["end"])

    typed_end = layout(RISHI_TYPES)[-1]
    final_lines = layout(HEADLINE)
    assert len(final_lines) == 4, "the headline must wrap into the site's four lines"
    claude = Track((RP + 40, fy + fh * .9), rng)
    claude.to(S(typed_end[2] - 70, typed_end[0] + 74), T["claude_in"], T["stream0"] - .05)
    claude.to(S(final_lines[-1][2] + 18, final_lines[-1][0] + 34), T["stream0"] + .1, T["streamed"])
    claude.to(S(140, 46), T["streamed"] + .05, T["nav"] - .05)
    claude.press(T["nav"] - .04)
    claude.to(S(560, 492), T["nav"] + .1, T["cur"] - .05)
    claude.press(T["cur"] - .04)
    claude.to((LP + 8, fy + fh + 12), T["cur"] + .15, T["chat"] - .05)

    # ---- assets ---------------------------------------------------------------------------------
    sky = cover(site_image(f"{site['sky']}.webp", f"/_next/image?url=%2Fhero%2F{site['sky']}.png&w=828&q=75"),
                round(780 * Z * 2), round(620 * Z * 2))
    tower = site_image("cn-tower.webp", "/hero/cn-tower-morning.webp").resize((76, 76), Image.LANCZOS)
    codex = site_image("codex.png", "/_next/image?url=%2Flogos%2Fborderless%2Fcodex.png&w=96&q=75").resize((64, 64), Image.LANCZOS)
    claude_logo = site_image("claude.png", "/_next/image?url=%2Flogos%2Fborderless%2Fclaude.png&w=96&q=75").resize((64, 64), Image.LANCZOS)
    star = "M8 0.9C8.75 4.05 10.95 6.35 15.1 8C10.95 9.65 8.75 11.95 8 15.1C7.25 11.95 5.05 9.65 0.9 8C5.05 6.35 7.25 4.05 8 0.9Z"

    def card(cx, cy, rot, inner):
        return (f'<g transform="translate({cx} {cy}) rotate({rot})"><rect x="-23.5" y="-22" width="47" height="47" rx="11.4" fill="#000" opacity=".1"/>'
                f'<rect x="-23.5" y="-23.5" width="47" height="47" rx="11.4" fill="{site["card"]}" stroke="{site["card_line"]}" stroke-width=".8"/>{inner}</g>')

    sc = site["scrim"]
    s.defs += [
        f'<image id="sky" width="780" height="620" preserveAspectRatio="none" href="{data_uri(sky, q=78)}"/>',
        '<clipPath id="cnc"><rect width="38" height="38" rx="11.4"/></clipPath>',
        f'<g id="cn"><rect x="7" y="1.5" width="38" height="38" rx="11.4" fill="#000" opacity=".12"/>'
        f'<g transform="translate(7 0)"><image width="38" height="38" clip-path="url(#cnc)" href="{data_uri(tower, q=85)}"/></g></g>',
        '<linearGradient id="spk" x1="0" y1="0" x2="1" y2=".84"><stop offset="0" stop-color="#FF375F"/><stop offset=".19" stop-color="#FF9F0A"/>'
        '<stop offset=".35" stop-color="#FFD60A"/><stop offset=".54" stop-color="#30D158"/><stop offset=".73" stop-color="#64D2FF"/>'
        '<stop offset="1" stop-color="#BF5AF2"/></linearGradient>',
        '<radialGradient id="spks" cx="5.2" cy="4.4" r="8.8" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#fff" stop-opacity=".85"/>'
        '<stop offset=".34" stop-color="#fff" stop-opacity=".18"/><stop offset=".72" stop-color="#fff" stop-opacity="0"/></radialGradient>',
        f'<g id="spark"><g transform="translate(3 3) scale(2.125)"><path d="{star}" fill="url(#spk)"/><path d="{star}" fill="url(#spks)"/></g></g>',
        '<g id="tools">'
        + card(28.5, 34.5, -7, f'<image x="-16" y="-16" width="32" height="32" href="{data_uri(codex, "PNG")}"/>')
        + card(57.5, 28.5, 4, '<g transform="translate(-11.5 -11.5) scale(.59)"><path d="M39 24H24V6H6V24H24V39H0V6H6V0H39V24Z" fill="#81ACEC"/></g>')
        + card(83, 33, -5, f'<image x="-15" y="-15" width="30" height="30" href="{data_uri(claude_logo, "PNG")}"/>') + "</g>",
        f'<linearGradient id="scrim" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{sc[0]}" stop-opacity="{sc[1]}"/>'
        f'<stop offset=".48" stop-color="{sc[0]}" stop-opacity="{sc[2]}"/><stop offset="1" stop-color="{sc[0]}" stop-opacity="{sc[3]}"/></linearGradient>',
        f'<clipPath id="win"><rect width="{W}" height="{H}" rx="12"/></clipPath>',
        f'<clipPath id="cv"><rect x="{LP + 1}" y="{TB}" width="{RP - LP - 2}" height="{H - TB}"/></clipPath>',
        '<clipPath id="fr"><rect width="780" height="684"/></clipPath>',
        '<clipPath id="sw"><rect width="14" height="14" rx="3"/></clipPath>',
        f'<filter id="sh" x="-20%" y="-40%" width="140%" height="200%"><feDropShadow dx="0" dy="4" stdDeviation="8" flood-color="#000" flood-opacity="{ui["shadow"]}"/></filter>',
    ]

    s.add('<g clip-path="url(#win)">', f'<rect width="{W}" height="{H}" fill="{ui["canvas"]}"/>')

    # ================================================================== canvas ===
    # 1. The frame follows Rishi's hand while it's dragged out.
    grow = [(0, "transform:scale(0,0)", "steps(1,end)"), (T["drag0"], "transform:scale(0,0)", None)]
    grow += [(t_, f"transform:scale({f((x - fx) / fw)},{f((y - fy) / fh)})", None) for t_, (x, y) in drag]
    s.add(f'<g transform="translate({f(fx)} {f(fy)})"><g class="{s.anim(grow)}"><g transform="scale({Z})">'
          f'<rect width="780" height="684" fill="{site["bg"]}"/>'
          f'<g {s.show([(T["drop"], None)])}><g clip-path="url(#fr)"><use href="#sky" y="64"/>'
          f'<rect y="64" width="780" height="620" fill="url(#scrim)"/></g></g></g></g></g>')
    s.add(f'<g {s.show([(T["drag0"] + .05, None)])}>' + s.text(fx, fy - 7, "Home", "ui", 11, ui["t3"]) + "</g>",
          f'<g {s.show([(T["drag0"] + .05, T["box0"]), (T["hover_frame"], None)])}>' + s.text(fx, fy - 7, "Home", "ui", 11, BLUE) + "</g>")

    # 2. The headline: Rishi types it key by key, Claude streams the rest, icons included.
    states = [(t_, RISHI_TYPES[:i + 1], "rishi") for i, t_ in enumerate(char_t)]
    full = RISHI_TYPES
    for chunk, t_ in zip(chunks, stream_t):
        full += chunk
        states.append((t_, full, "claude"))
    assert full == HEADLINE
    per_line: list[list] = [[] for _ in final_lines]
    for t_, tx, _ in states:
        rendered = render_headline(s, tx, site["ink"])
        for k in range(len(final_lines)):
            svg_k = rendered[k] if k < len(rendered) else ""
            if not per_line[k] or per_line[k][-1][1] != svg_k:
                per_line[k].append((t_, svg_k))
    s.add(f'<g transform="translate({f(fx)} {f(fy)}) scale({Z})">'
          + "".join(s.states([it for it in items if it[1]]) for items in per_line) + "</g>")

    def caret(markup, color):
        base, _, x = layout(markup)[-1]
        return f'<rect x="{f(x + 1)}" y="{f(base - 31)}" width="2.2" height="40" fill="{color}"/>'

    blink, tb_ = [], T["typed"]
    while tb_ < T["stream0"] - .05:
        blink.append((tb_, min(tb_ + .5, T["stream0"] - .05)))
        tb_ += 1.0
    carets = [(T["box1"], caret("", BLUE))] + [(t_ + .001, caret(tx, BLUE if who == "rishi" else CLAUDE)) for t_, tx, who in states]
    s.add(f'<g {s.show([(T["box1"], T["typed"])] + blink + [(T["stream0"], T["streamed"] + .05)])}>'
          f'<g transform="translate({f(fx)} {f(fy)}) scale({Z})">' + s.states(carets) + "</g></g>")

    def text_box(markup):
        last = layout(markup)[-1][0]
        x1, y1 = S(H_BOX[1] + 4, last + 14)
        return f'<rect x="{f(bx0)}" y="{f(by0)}" width="{f(x1 - bx0)}" height="{f(y1 - by0)}" stroke="{BLUE}"/>'

    s.add(f'<g {s.show([(T["box0"], T["box1"])])}>'
          + s.states([(T["box0"], "")] + [(t_, f'<rect x="{f(bx0)}" y="{f(by0)}" width="{f(max(1, x - bx0))}" height="{f(max(1, y - by0))}" stroke="{BLUE}"/>')
                                         for t_, (x, y) in bdrag]) + "</g>")
    s.add(f'<g {s.show([(T["box1"], T["sel_frame"])])}>'
          + s.states([(T["box1"], text_box(""))] + [(t_ + .001, text_box(tx)) for t_, tx, _ in states]) + "</g>")

    # 3. Claude adds the nav and the "Currently" line.
    nav = (f'<rect width="780" height="64" fill="{site["nav"]}"/><rect y="63.5" width="780" height="1" fill="{site["nav_line"]}"/>'
           + s.text(24, 37, "Rishi Ashar", "site", 14, site["name"])
           + "".join(f'<circle cx="{cx}" cy="32" r="16" fill="{site["btn"]}" stroke="{site["btn_line"]}"/>' for cx in (369, 409))
           + f'<path d="M361.5 29 H364.5 L368.5 25.5 V38.5 L364.5 35 H361.5Z" stroke="{site["btn_icon"]}" stroke-width="1.3" stroke-linejoin="round"/>'
           + f'<path d="M371.5 29.5 L376.5 34.5 M376.5 29.5 L371.5 34.5" stroke="{site["btn_icon"]}" stroke-width="1.3" stroke-linecap="round"/>'
           + f'<path d="M413 37.2 A7 7 0 1 1 405.2 26 A5.6 5.6 0 0 0 413 37.2Z" fill="{site["btn_icon"]}"/>'
           + s.text(669, 37, "play", "site", 14, site["link"]) + s.text(716, 37, "about", "site", 14, site["link"]))
    cur_w1 = FONTS["serif"].width("Experience Design", 14)
    ad_w = 13 + 5 + FONTS["ui-sb"].width("AUTODESK", 11, .5)
    rx0 = 388.5 - (cur_w1 + 12 + ad_w) / 2
    ax = rx0 + cur_w1 + 12
    current = (s.text(388.5, 470, "Currently", "serif", 20, site["ink"], "middle", tracking=-.2)
               + s.text(rx0, 497, "Experience Design", "serif", 14, site["ink"])
               + f'<path d="M{f(ax)} 497 L{f(ax + 7)} 486.5 H{f(ax + 13)} L{f(ax + 6)} 497Z M{f(ax + 4.5)} 497 L{f(ax + 9)} 490.5 H{f(ax + 13)} V497Z" fill="{site["ink"]}"/>'
               + s.text(ax + 18, 497, "AUTODESK", "ui-sb", 11, site["ink"], tracking=.5))
    s.add(f'<g transform="translate({f(fx)} {f(fy)}) scale({Z})"><g {s.show([(T["nav"], None)])}>{nav}</g>'
          f'<g {s.show([(T["cur"], None)])}>{current}</g></g>')
    for (x0, y0, x1, y1), t_ in (((0, 0, 780, 64), T["nav"]), ((rx0 - 6, 446, 780 - rx0 + 6, 504), T["cur"])):
        a, b = S(x0, y0), S(x1, y1)
        s.add(f'<rect {s.show([(t_, t_ + .4)])} x="{f(a[0])}" y="{f(a[1])}" width="{f(b[0] - a[0])}" height="{f(b[1] - a[1])}" stroke="{CLAUDE}" stroke-width="1.5"/>')

    # drop target while the file hovers the frame
    s.add(f'<rect {s.show([(T["in1"] - .2, T["drop"])])} x="{f(fx + 1)}" y="{f(fy + 1)}" width="{f(fw - 2)}" height="{f(fh - 2)}" stroke="{BLUE}" stroke-width="2"/>')

    # Rishi's selection on the frame: while drawing it, and again at the end
    hs = 7

    def handles(w_, h_):
        return "".join(f'<rect x="{f(fx + ox * w_ - hs / 2)}" y="{f(fy + oy * h_ - hs / 2)}" width="{hs}" height="{hs}" fill="#fff" stroke="{BLUE}"/>'
                       for ox, oy in ((0, 0), (1, 0), (0, 1), (1, 1)))

    def badge(cx, top, label):
        bw = FONTS["ui-md"].width(label, 11) + 10
        return (f'<rect x="{f(cx - bw / 2)}" y="{f(top)}" width="{f(bw)}" height="17" rx="3" fill="{BLUE}"/>'
                + s.text(cx, top + 12.2, label, "ui-md", 11, "#fff", "middle", num=True))

    def selection(w_, h_):
        return (f'<rect x="{f(fx)}" y="{f(fy)}" width="{f(w_)}" height="{f(h_)}" stroke="{BLUE}"/>' + handles(w_, h_)
                + badge(fx + w_ / 2, fy + h_ + 8, f"{round(w_ / Z)} × {round(h_ / Z)}"))

    live = [(T["drag0"], "")] + [(t_, selection(max(1, x - fx), max(1, y - fy))) for t_, (x, y) in drag[:-1]]
    s.add(f'<g {s.show([(T["drag0"], T["box0"])])}>' + s.states(live + [(drag[-1][0], selection(fw, fh))]) + "</g>",
          f'<rect {s.show([(T["hover_frame"], T["sel_frame"])])} x="{f(fx)}" y="{f(fy)}" width="{f(fw)}" height="{f(fh)}" stroke="{BLUE}"/>',
          f'<g {s.show([(T["sel_frame"], None)])}>{selection(fw, fh)}</g>')

    # ---- floating toolbar --------------------------------------------------------------------
    cells, x = [], 8
    for name, has_chev in (("move", True), ("frame", True), ("rect", True), ("pen", True), ("text", False), ("comment", False)):
        w_ = 42 if has_chev else 32
        cells.append((name, x, w_, has_chev))
        x += w_ + 2
    sep, act_x = x + 3, x + 8
    dev_x = act_x + 34
    tb_w = dev_x + 54
    tb_x, tb_y = round((LP + RP) / 2 - tb_w / 2), H - 58
    s.add(f'<g transform="translate({tb_x} {tb_y})"><rect width="{tb_w}" height="46" rx="13" fill="{ui["panel"]}" stroke="{ui["border"]}" stroke-width=".8" filter="url(#sh)"/>')
    active = {"move": [(0, T["frame_key"]), (T["drag1"] + .05, T["text_key"]), (T["esc"], None)],
              "frame": [(T["frame_key"], T["drag1"] + .05)], "text": [(T["text_key"], T["esc"])]}

    def tool(name, cx, has_chev, color):
        return (f'<g transform="translate({cx + 16} 23)">{TOOL_ICONS[name](color)}</g>'
                + (chev(cx + 34, 23.5, color, 2.4) if has_chev else ""))

    for name, cx, w_, has_chev in cells:
        if name in active:
            s.add(f'<g {s.show(complement(active[name]))}>{tool(name, cx, has_chev, ui["icon"])}</g>',
                  f'<g {s.show(active[name])}><rect x="{cx}" y="7" width="{w_}" height="32" rx="6" fill="{BLUE}"/>{tool(name, cx, has_chev, "#fff")}</g>')
        else:
            s.add(tool(name, cx, has_chev, ui["icon"]))
    s.add(f'<line x1="{sep + .5}" y1="13" x2="{sep + .5}" y2="33" stroke="{ui["border"]}"/>',
          f'<g transform="translate({act_x + 16} 23)"><path d="M0 -7 C.6 -2.4 2.4 -.6 7 0 C2.4 .6 .6 2.4 0 7 C-.6 2.4 -2.4 .6 -7 0 C-2.4 -.6 -.6 -2.4 0 -7Z" stroke="{ui["icon"]}" stroke-width="1.15" stroke-linejoin="round"/></g>',
          f'<g transform="translate({dev_x + 22} 23)"><rect x="-17" y="-10" width="34" height="20" rx="10" fill="{ui["field"]}" stroke="{ui["border"]}" stroke-width=".6"/>'
          f'<circle cx="-7" cy="0" r="8" fill="{ui["panel"]}" stroke="{ui["border"]}" stroke-width=".6"/><path d="M-9.5 -2.2 L-11.6 0 L-9.5 2.2 M-4.5 -2.2 L-2.4 0 L-4.5 2.2 M-6.2 -3 L-7.8 3" '
          f'stroke="{ui["t2"]}" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"/></g></g>')

    # ---- Claude's cursor, which lives on the canvas ----------------------------------------------
    cm, cp = claude.css(s)
    cidle = idle_loop(s, rng, T["end"], 11, [(2.5, (10, -4)), (7.2, (2, 5))])
    tag_w = FONTS["ui-md"].width("Claude", 11) + 12
    chat_items, ct = [], T["chat"]
    for i in range(1, len(CHAT) + 1):            # cursor chat grows as it's typed
        part = CHAT[:i]
        chat_items.append((ct, f'<rect x="10" y="17" width="{f(FONTS["ui"].width(part, 12) + 22)}" height="26" rx="13" fill="{CLAUDE}"/>'
                           + s.text(21, 34.5, part, "ui", 12, "#fff")))
        ct += .028 + (.05 if CHAT[i - 1] == " " else 0)
    s.add(f'<g clip-path="url(#cv)"><g class="{cm}" style="transform:translate({f(claude.pos[0])}px,{f(claude.pos[1])}px)"><g class="{cidle}">'
          f'<g class="{cp}"><path d="{CURSOR}" fill="{CLAUDE}" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/></g>'
          f'<g {s.show([(0, T["chat"]), (T["chat_end"], None)])}><rect x="10" y="17" width="{f(tag_w)}" height="18" rx="4" fill="{CLAUDE}"/>'
          + s.text(16, 29.5, "Claude", "ui-md", 11, "#fff") + "</g>"
          + f'<g {s.show([(T["chat"], T["chat_end"])])}>' + s.states(chat_items) + "</g></g></g></g>")

    # ================================================================== chrome ===
    s.add(f'<rect width="{W}" height="{TB}" fill="{ui["tabbar"]}"/>')
    for i, col in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        s.add(f'<circle cx="{18 + i * 18}" cy="18" r="5.5" fill="{col}"/>')
    tab_x, tab_w = 104, 188
    s.add(f'<path d="M80 19.5 L86 14 L92 19.5 V24 H80Z" stroke="{ui["t2"]}" stroke-width="1.1" stroke-linejoin="round"/>',
          f'<path d="M{tab_x} {TB} V12 Q{tab_x} 6 {tab_x + 6} 6 H{tab_x + tab_w - 6} Q{tab_x + tab_w} 6 {tab_x + tab_w} 12 V{TB}Z" fill="{ui["panel"]}"/>',
          figma_mark(tab_x + 12, 15, .5, ui["t2"]), s.text(tab_x + 26, 24, "rishiashar.com", "ui-md", 11, ui["t1"]),
          f'<path d="M{tab_x + tab_w - 18} 17.5 l5 5 m0 -5 l-5 5" stroke="{ui["t2"]}" stroke-width="1.1" stroke-linecap="round"/>',
          plus(tab_x + tab_w + 18, 21, ui["t2"]))

    # left panel
    s.add(f'<rect x="0" y="{TB}" width="{LP}" height="{H - TB}" fill="{ui["panel"]}"/>'
          f'<line x1="{LP + .5}" y1="{TB}" x2="{LP + .5}" y2="{H}" stroke="{ui["border"]}" stroke-width=".8"/>',
          figma_mark(16, 50, .78, ui["t1"]), chev(36, 57, ui["t2"], 2.6),
          f'<rect x="{LP - 30}" y="49" width="14" height="14" rx="2.5" stroke="{ui["t2"]}" stroke-width="1.1"/>'
          f'<line x1="{LP - 25.5}" y1="49" x2="{LP - 25.5}" y2="63" stroke="{ui["t2"]}" stroke-width="1.1"/>',
          s.text(16, 92, "rishiashar.com", "ui-sb", 12, ui["t1"]), chev(16 + FONTS["ui-sb"].width("rishiashar.com", 12) + 9, 88, ui["t2"], 2.8),
          s.text(16, 108, "Portfolio", "ui", 11, ui["t3"]), f'<line x1="0" y1="122.5" x2="{LP}" y2="122.5" stroke="{ui["div"]}"/>')
    fw_ = FONTS["ui-sb"].width("File", 11)
    s.add(f'<rect x="10" y="129" width="{f(fw_ + 16)}" height="24" rx="5" fill="{ui["field"]}"/>',
          s.text(18, 145, "File", "ui-sb", 11, ui["t1"]), s.text(18 + fw_ + 22, 145, "Assets", "ui-md", 11, ui["t3"]),
          f'<circle cx="{LP - 24}" cy="140" r="4.5" stroke="{ui["t2"]}" stroke-width="1.1"/><line x1="{LP - 20.5}" y1="143.5" x2="{LP - 17}" y2="147" stroke="{ui["t2"]}" stroke-width="1.1" stroke-linecap="round"/>',
          s.text(16, 178, "Pages", "ui-sb", 11, ui["t1"]), plus(LP - 22, 174, ui["t2"]))
    for i, pg in enumerate(["Home", "Work", "Play", "About"]):
        y = 186 + i * 24
        if i == 0:
            s.add(f'<path d="M15 {y + 12} l2.6 2.6 l4.6 -5.2" stroke="{ui["t1"]}" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>')
        s.add(s.text(30, y + 16, pg, "ui-md" if i == 0 else "ui", 11, ui["t1"] if i == 0 else ui["t2"]))
    s.add(f'<line x1="0" y1="290.5" x2="{LP}" y2="290.5" stroke="{ui["div"]}"/>', s.text(16, 314, "Layers", "ui-sb", 11, ui["t1"]))

    # layers: Home, then its children newest-first, like Figma
    ROW, ly0 = 27, 324
    kids = [("image", site["sky"], T["drop"]), ("text", "headline", T["box0"]), ("instance", "Nav", T["nav"]), ("auto", "Currently", T["cur"])]

    def row_y(i, t_):
        return ly0 + ROW * (1 + sum(1 for k in kids[i + 1:] if k[2] <= t_))

    s.add(f'<rect {s.show([(T["drag0"], T["box0"]), (T["sel_frame"], None)])} x="8" y="{ly0}" width="{LP - 16}" height="{ROW - 2}" rx="5" fill="{ui["row"]}"/>',
          f'<g {s.show([(T["drag0"], None)])}>{chev(16, ly0 + 12.5, ui["t2"], 2.4)}<g transform="translate(29 {ly0 + 12.5})">{LAYER_ICONS["frame"](ui["t1"])}</g>'
          + s.text(42, ly0 + 16.5, "Home", "ui-md", 11, ui["t1"]) + "</g>")
    hl = [(0, f"opacity:0;transform:translateY({row_y(1, 0)}px)", "steps(1,end)"),
          (T["box0"], f"opacity:1;transform:translateY({row_y(1, T['box0'])}px)", "steps(1,end)")]
    hl += [(k[2], f"opacity:1;transform:translateY({row_y(1, k[2])}px)", "steps(1,end)") for k in kids[2:]]
    hl.append((T["sel_frame"], f"opacity:0;transform:translateY({row_y(1, T['sel_frame'])}px)", None))
    s.add(f'<rect class="{s.anim(hl)}" style="opacity:0" x="8" y="0" width="{LP - 16}" height="{ROW - 2}" rx="5" fill="{ui["row"]}"/>')
    label_w = LP - 56 - 14

    def layer_name(markup):     # Figma names a text layer after its content until you rename it
        text_ = " ".join(markup.replace("{cn}", "").replace("{spark}", "").replace("{tools}", "").replace("|", "").split())
        if FONTS["ui"].width(text_, 11) <= label_w:
            return text_
        while FONTS["ui"].width(text_ + "…", 11) > label_w:
            text_ = text_[:-1]
        return text_.rstrip() + "…"

    for i, (kind, label, t0) in enumerate(kids):
        fr = [(0, f"opacity:0;transform:translateY({row_y(i, 0)}px)", "steps(1,end)"),
              (t0, f"opacity:1;transform:translateY({row_y(i, t0)}px)", "steps(1,end)")]
        fr += [(k[2], f"opacity:1;transform:translateY({row_y(i, k[2])}px)", "steps(1,end)") for k in kids[i + 1:]]
        fr.append((T["end"], f"opacity:1;transform:translateY({row_y(i, T['end'])}px)", None))
        color = ui["instance"] if kind == "instance" else ui["t2"]
        if label == "headline":
            names, last = [(T["box0"], s.text(56, 16.5, "Text", "ui", 11, color))], "Text"
            for t_, tx, _ in states:
                lab = layer_name(tx)
                if lab != last:
                    names.append((t_, s.text(56, 16.5, lab, "ui", 11, color)))
                    last = lab
            body = s.states(names)
        else:
            body = s.text(56, 16.5, label, "ui", 11, color)
        s.add(f'<g class="{s.anim(fr)}" style="transform:translateY({row_y(i, T["end"])}px)"><g transform="translate(43 12.5)">{LAYER_ICONS[kind](color)}</g>{body}</g>')

    # right panel: header and tabs
    s.add(f'<rect x="{RP}" y="{TB}" width="{W - RP}" height="{H - TB}" fill="{ui["panel"]}"/>'
          f'<line x1="{RP - .5}" y1="{TB}" x2="{RP - .5}" y2="{H}" stroke="{ui["border"]}" stroke-width=".8"/>',
          f'<g transform="translate({px0 + 30} 60)"><g {s.show([(T["claude_in"], None)])}><circle r="12" fill="{CLAUDE}" stroke="{ui["panel"]}" stroke-width="2"/>{claude_burst(7)}</g></g>',
          f'<g transform="translate({px0 + 12} 60)"><circle r="12" fill="{RISHI}" stroke="{ui["panel"]}" stroke-width="2"/>' + s.text(0, 4, "R", "ui-sb", 11, "#fff", "middle") + "</g>",
          f'<path d="M{px1 - 91} 54 L{px1 - 82} 60 L{px1 - 91} 66Z" stroke="{ui["t1"]}" stroke-width="1.15" stroke-linejoin="round"/>', chev(px1 - 73, 60.5, ui["t2"], 2.4),
          f'<rect x="{px1 - 58}" y="47" width="58" height="26" rx="6" fill="{BLUE}"/>', s.text(px1 - 29, 64, "Share", "ui-md", 11, "#fff", "middle"),
          f'<rect {s.show([(0, T["proto"])])} x="{px0 - 2}" y="{tabs_y - 16}" width="{f(wd + 16)}" height="24" rx="5" fill="{ui["field"]}"/>',
          f'<rect {s.show([(T["tab_hover"], None)])} x="{f(px0 + wd + 18)}" y="{tabs_y - 16}" width="{f(wp + 16)}" height="24" rx="5" fill="{ui["field"]}"/>',
          s.text(px0 + 6, tabs_y, "Design", "ui-sb", 11, ui["t3"]), f'<g {s.show([(0, T["proto"])])}>' + s.text(px0 + 6, tabs_y, "Design", "ui-sb", 11, ui["t1"]) + "</g>",
          s.text(px0 + wd + 26, tabs_y, "Prototype", "ui-sb", 11, ui["t3"]), f'<g {s.show([(T["proto"], None)])}>' + s.text(px0 + wd + 26, tabs_y, "Prototype", "ui-sb", 11, ui["t1"]) + "</g>",
          s.text(px1 - 12, tabs_y, "46%", "ui", 11, ui["t2"], "end", num=True), chev(px1 - 3, tabs_y - 3.5, ui["t2"], 2.4),
          f'<line x1="{RP}" y1="120.5" x2="{W}" y2="120.5" stroke="{ui["div"]}"/>')

    def field(x, y, w_, h_=26):
        return f'<rect x="{f(x)}" y="{f(y)}" width="{f(w_)}" height="{h_}" rx="5" fill="{ui["field"]}"/>'

    def kv(x, y, w_, k, v):
        return field(x, y, w_) + s.text(x + 9, y + 17, k, "ui", 11, ui["t3"]) + s.text(x + 24, y + 17, v, "ui", 11, ui["t1"], num=True)

    def head(y, label, icon=""):
        return s.text(px0 + 4, y, label, "ui-sb", 11, ui["t1"]) + icon

    def div(y):
        return f'<line x1="{RP}" y1="{y + .5}" x2="{W}" y2="{y + .5}" stroke="{ui["div"]}"/>'

    def swatch(y, label, paint):
        return (field(px0, y, 158) + paint(px0 + 7, y + 6) + s.text(px0 + 29, y + 17, label, "ui", 11, ui["t1"])
                + s.text(px0 + 150, y + 17, "100%", "ui", 11, ui["t1"], "end", num=True) + eye(px1 - 34, y + 13, ui["t2"])
                + f'<path d="M{px1 - 13} {y + 13} H{px1 - 5}" stroke="{ui["t2"]}" stroke-width="1.1" stroke-linecap="round"/>')

    def solid(c):
        return lambda x, y: f'<rect x="{x}" y="{y}" width="14" height="14" rx="3" fill="{c}" stroke="{ui["border"]}"/>'

    def image_swatch(x, y):
        return f'<g transform="translate({x} {y})"><g clip-path="url(#sw)"><use href="#sky" transform="scale(.0226)"/></g></g>'

    hw = (px1 - px0 - 8) / 2
    page = [head(146, "Page"), swatch(156, ui["canvas_hex"], solid(ui["canvas"])), div(194),
            head(218, "Variables", f'<path d="M{px1 - 17} 211 H{px1 - 3} M{px1 - 17} 219 H{px1 - 3}" stroke="{ui["t2"]}" stroke-width="1.1" stroke-linecap="round"/>'
                 f'<circle cx="{px1 - 13}" cy="211" r="2.2" fill="{ui["panel"]}" stroke="{ui["t2"]}" stroke-width="1.1"/><circle cx="{px1 - 7}" cy="219" r="2.2" fill="{ui["panel"]}" stroke="{ui["t2"]}" stroke-width="1.1"/>'),
            div(234), head(258, "Styles", plus(px1 - 10, 254, ui["t2"])), div(274), head(298, "Export", plus(px1 - 10, 294, ui["t2"]))]
    align = "".join(f'<path d="{d}" stroke="{ui["t2"]}" stroke-width="1.1" stroke-linecap="round"/>' for d in (
        f"M{px0 + 12} 189 V201 M{px0 + 15} 192 H{px0 + 22} M{px0 + 15} 198 H{px0 + 20}",
        f"M{px0 + 40} 189 V201 M{px0 + 36} 192 H{px0 + 44} M{px0 + 37} 198 H{px0 + 43}",
        f"M{px0 + 66} 189 V201 M{px0 + 56} 192 H{px0 + 63} M{px0 + 58} 198 H{px0 + 63}",
        f"M{px0 + 82} 188 H{px0 + 94} M{px0 + 85} 191 V198 M{px0 + 91} 191 V196",
        f"M{px0 + 104} 194 H{px0 + 116} M{px0 + 107} 190 V198 M{px0 + 113} 191 V197",
        f"M{px0 + 126} 200 H{px0 + 138} M{px0 + 129} 190 V197 M{px0 + 135} 192 V197"))

    def wh(w_, h_):
        return kv(px0, 286, hw, "W", str(round(w_))) + kv(px0 + hw + 8, 286, hw, "H", str(round(h_)))

    frame = [head(146, "Frame", chev(px0 + 4 + FONTS["ui-sb"].width("Frame", 11) + 9, 142.5, ui["t2"], 2.6)),
             head(176, "Position"), field(px0, 182, px1 - px0), align, kv(px0, 214, hw, "X", "0"), kv(px0 + hw + 8, 214, hw, "Y", "0"), div(252),
             head(276, "Layout"),
             s.states([(T["drag0"], wh(0, 0))] + [(t_, wh((x - fx) / Z, (y - fy) / Z)) for t_, (x, y) in drag[:-1]] + [(drag[-1][0], wh(780, 684))]),
             div(324), head(348, "Appearance"), field(px0, 358, hw),
             f'<rect x="{px0 + 8}" y="365" width="11" height="11" rx="2" stroke="{ui["t3"]}" stroke-width="1.1" stroke-dasharray="2 1.5"/>',
             s.text(px0 + 26, 375, "100%", "ui", 11, ui["t1"], num=True), field(px0 + hw + 8, 358, hw),
             f'<path d="M{f(px0 + hw + 17)} 377 V370 Q{f(px0 + hw + 17)} 365 {f(px0 + hw + 22)} 365 H{f(px0 + hw + 28)}" stroke="{ui["t3"]}" stroke-width="1.1" stroke-linecap="round"/>',
             s.text(px0 + hw + 34, 375, "0", "ui", 11, ui["t1"], num=True), div(396), head(420, "Fill", plus(px1 - 10, 416, ui["t2"])),
             s.states([(0, swatch(430, site["bg"][1:], solid(site["bg"]))), (T["drop"], swatch(430, "Image", image_swatch))])]
    text_panel = [head(146, "Text"), head(176, "Position"), kv(px0, 186, hw, "X", "125"), kv(px0 + hw + 8, 186, hw, "Y", "218"), div(224),
                  head(248, "Typography"), field(px0, 258, px1 - px0), s.text(px0 + 9, 275, "Fraunces", "ui", 11, ui["t1"]), chev(px1 - 12, 271, ui["t2"], 2.6),
                  field(px0, 290, hw + 30), s.text(px0 + 9, 307, "Regular", "ui", 11, ui["t1"]), chev(px0 + hw + 20, 303, ui["t2"], 2.6),
                  field(px0 + hw + 38, 290, hw - 30), s.text(px0 + hw + 47, 307, "38", "ui", 11, ui["t1"], num=True), chev(px1 - 12, 303, ui["t2"], 2.6),
                  field(px0, 322, hw), f'<path d="M{px0 + 8} 329 H{px0 + 18} M{px0 + 8} 341 H{px0 + 18} M{px0 + 13} 332 V338" stroke="{ui["t3"]}" stroke-width="1.1" stroke-linecap="round"/>',
                  s.text(px0 + 24, 339, "116%", "ui", 11, ui["t1"], num=True),
                  field(px0 + hw + 8, 322, hw), s.text(px0 + hw + 16, 339, "|A|", "ui", 10, ui["t3"]), s.text(px0 + hw + 36, 339, "-1.5%", "ui", 11, ui["t1"], num=True),
                  field(px0, 354, px1 - px0)]
    seg = (px1 - px0 - 8) / 4
    for k in range(4):                       # text alignment: centre is on, like the site
        x0 = px0 + 4 + k * seg
        if k == 1:
            text_panel.append(f'<rect x="{f(x0)}" y="357" width="{f(seg)}" height="20" rx="4" fill="{ui["panel"]}"/>')
        cx = x0 + seg / 2
        lines = ""
        for j, ln in enumerate([(10, 7, 10), (10, 6, 8), (10, 7, 10), (10, 10, 10)][k]):
            xs = (cx - 5, cx - ln / 2, cx + 5 - ln, cx - 5)[k]
            lines += f"M{f(xs)} {362 + j * 4} H{f(xs + ln)} "
        text_panel.append(f'<path d="{lines}" stroke="{ui["t1"] if k == 1 else ui["t2"]}" stroke-width="1.1" stroke-linecap="round"/>')
    text_panel += [div(392), head(416, "Fill", plus(px1 - 10, 412, ui["t2"])), swatch(426, site["ink"][1:], solid(site["ink"]))]

    grid = "".join(f'<line x1="{f(ex0 + ew * k / 4)}" y1="{ey0}" x2="{f(ex0 + ew * k / 4)}" y2="{ey0 + eh}" stroke="{ui["grid"]}"/>' for k in (1, 2, 3))
    grid += "".join(f'<line x1="{ex0}" y1="{f(ey0 + eh * k / 3)}" x2="{ex0 + ew}" y2="{f(ey0 + eh * k / 3)}" stroke="{ui["grid"]}"/>' for k in (1, 2))
    fy2 = ey0 + eh + 8
    ax_ = px0 + 26 + FONTS["ui"].width("After delay", 11) + 8
    proto = [head(146, "Interactions", plus(px1 - 10, 142, ui["t2"])), field(px0, 156, px1 - px0, 30),
             f'<circle cx="{px0 + 14}" cy="171" r="5.5" stroke="{ui["t2"]}" stroke-width="1.1"/><path d="M{px0 + 14} 168 V171.3 L{px0 + 16.2} 172.6" stroke="{ui["t2"]}" stroke-width="1.1" stroke-linecap="round"/>',
             s.text(px0 + 26, 175, "After delay", "ui", 11, ui["t2"]),
             f'<path d="M{f(ax_)} 171 H{f(ax_ + 10)} M{f(ax_ + 6.5)} 167.5 L{f(ax_ + 10)} 171 L{f(ax_ + 6.5)} 174.5" stroke="{ui["t3"]}" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>',
             s.text(ax_ + 17, 175, "Home", "ui-md", 11, ui["t1"]), div(198), head(222, "Animation"),
             field(px0, 232, px1 - px0), s.text(px0 + 9, 249, "Smart animate", "ui", 11, ui["t1"]), chev(px1 - 12, 245, ui["t2"], 2.6),
             field(px0, 264, px1 - px0), f'<path d="M{px0 + 8} 283 C{px0 + 11} 273 {px0 + 14} 271 {px0 + 20} 271" stroke="{ui["t2"]}" stroke-width="1.1" stroke-linecap="round"/>',
             s.text(px0 + 26, 281, "Custom bezier", "ui", 11, ui["t1"]), chev(px1 - 12, 277, ui["t2"], 2.6),
             f'<rect x="{ex0}" y="{ey0}" width="{ew}" height="{eh}" rx="6" fill="{ui["editor"]}" stroke="{ui["div"]}"/>', grid,
             f'<path d="M{f(q0[0])} {f(q0[1])} L{f(q3[0])} {f(q3[1])}" stroke="{ui["t3"]}" stroke-opacity=".5" stroke-dasharray="2 3"/>',
             f'<line x1="{f(q3[0])}" y1="{f(q3[1])}" x2="{f(p2[0])}" y2="{f(p2[1])}" stroke="{BLUE}"/>',
             f'<circle cx="{f(p2[0])}" cy="{f(p2[1])}" r="4" fill="{ui["editor"]}" stroke="{BLUE}" stroke-width="1.5"/>',
             f'<circle cx="{f(q0[0])}" cy="{f(q0[1])}" r="2.5" fill="{ui["curve"]}"/><circle cx="{f(q3[0])}" cy="{f(q3[1])}" r="2.5" fill="{ui["curve"]}"/>',
             field(px0, fy2, 150), field(px0 + 158, fy2, px1 - px0 - 158), s.text(px0 + 167, fy2 + 17, "600ms", "ui", 11, ui["t1"], num=True)]

    for body, wins in ((page, [(0, T["drag0"])]), (frame, [(T["drag0"], T["box0"]), (T["sel_frame"], T["proto"])]),
                       (text_panel, [(T["box0"], T["sel_frame"])]), (proto, [(T["proto"], None)])):
        s.add(f'<g {s.show(wins)}>{"".join(body)}</g>')

    # the handle follows Rishi's hand; the curve and its numbers follow the handle
    def curve_state(p1):
        return (f'<line x1="{f(q0[0])}" y1="{f(q0[1])}" x2="{f(p1[0])}" y2="{f(p1[1])}" stroke="{BLUE}"/>'
                f'<path d="M{f(q0[0])} {f(q0[1])} C{f(p1[0])} {f(p1[1])} {f(p2[0])} {f(p2[1])} {f(q3[0])} {f(q3[1])}" stroke="{ui["curve"]}" stroke-width="2" stroke-linecap="round"/>'
                f'<circle cx="{f(p1[0])}" cy="{f(p1[1])}" r="4" fill="#fff" stroke="{BLUE}" stroke-width="1.5"/>')

    def num(v):
        return str(round(v)) if abs(v - round(v)) < .005 else f"{v:.2f}".rstrip("0")

    def values(p1):
        return s.text(px0 + 9, fy2 + 17, f"{num((p1[0] - q0[0]) / ddx)}, {num((p1[1] - q0[1]) / ddy)}, 0.3, 1", "ui", 11, ui["t1"], num=True)

    s.add(f'<g {s.show([(T["proto"], None)])}>'
          + s.states([(0, curve_state(p1a))] + [(t_, curve_state(p)) for t_, p in bdr[:-1]] + [(bdr[-1][0], curve_state(p1b))])
          + s.states([(0, values(p1a))] + [(t_, values(p)) for t_, p in bdr[:-1]] + [(bdr[-1][0], values(p1b))]) + "</g>")
    # a quiet preview of the curve: one run each way, then a long rest
    tr0, tr1, tr_y = px0 + 10, px1 - 10, fy2 + 46
    L = tr1 - tr0
    loop = [(0, "transform:translateX(0)", OUT_EXPO), (.6, f"transform:translateX({f(L)}px)", None),
            (1.6, f"transform:translateX({f(L)}px)", OUT_EXPO), (2.2, "transform:translateX(0)", None), (3.2, "transform:translateX(0)", None)]
    s.add(f'<g {s.show([(T["proto"], None)])}><line x1="{tr0}" y1="{tr_y}" x2="{tr1}" y2="{tr_y}" stroke="{ui["field"]}" stroke-width="2" stroke-linecap="round"/>'
          f'<g transform="translate({tr0} {tr_y})"><g class="{s.anim(loop, iterations="infinite", delay=T["end"])}">'
          f'<circle r="8" fill="{BLUE}" opacity=".18"/><circle r="4.5" fill="{ui["curve"]}"/></g></g></g>')

    # ---- Rishi's cursor, on top of everything ---------------------------------------------------
    rm, rp = rishi.css(s)
    ridle = idle_loop(s, rng, T["end"], 9, [(3.1, (-12, 5)), (6.4, (-4, -3))])
    rw = FONTS["ui-md"].width("Rishi", 11) + 12
    thumb = (f'<g {s.show([(T["in0"], T["drop"])])}><g transform="translate(14 12)"><rect x="-1" y="1.5" width="58" height="46" rx="5" fill="#000" opacity=".25"/>'
             f'<g opacity=".92"><use href="#sky" transform="scale(.0718)"/></g><rect width="56" height="44.5" rx="4" stroke="#fff" stroke-opacity=".7"/>'
             f'<g {s.show([(T["in1"] - .2, T["drop"])])}><circle cx="56" cy="0" r="7" fill="#28C840" stroke="#fff" stroke-width="1.2"/>'
             f'<path d="M52.8 0 H59.2 M56 -3.2 V3.2" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/></g></g></g>')
    s.add(f'<g class="{rm}" style="transform:translate({f(rishi.pos[0])}px,{f(rishi.pos[1])}px)"><g class="{ridle}">{thumb}'
          f'<g class="{rp}"><path d="{CURSOR}" fill="{RISHI}" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/></g>'
          f'<rect x="10" y="17" width="{f(rw)}" height="18" rx="4" fill="{RISHI}"/>' + s.text(16, 29.5, "Rishi", "ui-md", 11, "#fff") + "</g></g>")

    s.add("</g>", f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" stroke="{ui["window"]}"/>')
    OUT.mkdir(exist_ok=True)
    data = s.render()
    (OUT / f"hero-{mode}.svg").write_text(data, encoding="utf-8")
    print(f"hero-{mode}.svg  {len(data) / 1024:.0f} KB, story {T['end']:.1f}s")


if __name__ == "__main__":
    for mode in ("light", "dark"):
        hero(mode)
