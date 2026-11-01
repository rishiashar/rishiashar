#!/usr/bin/env python3
"""
Builds the animated SVGs used by the profile README.

    pip install fonttools brotli
    python3 scripts/build.py

Everything is drawn by hand in SVG + CSS keyframes (plus a little SMIL),
so it renders inside GitHub's <img> sandbox: no JS, no external requests.
Fonts (Geist, Geist Mono, Fraunces) are subset to the glyphs each file
uses and embedded as base64 WOFF2. They're fetched from npm on first run
and cached in scripts/.cache.
"""
from __future__ import annotations

import base64
import io
import math
import tarfile
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"
CACHE = ROOT / "scripts" / ".cache"

# ---------------------------------------------------------------- palette ---
BG = "#0B0B0D"
PANEL = "#0F0F11"
LINE = "#202024"
LINE_2 = "#2A2A2F"
TEXT = "#EDEDEF"
TEXT_2 = "#A1A1AA"
TEXT_3 = "#6B6B74"
BLUE = "#0D99FF"      # selection
RISHI = "#7C5CFF"     # Rishi's cursor
CLAUDE = "#D97757"    # Claude's cursor
TEAL = "#2DD4BF"
AMBER = "#F5A524"

# ------------------------------------------------------------------ fonts ---
PACKAGES = {
    "geist": "https://registry.npmjs.org/geist/-/geist-1.9.0.tgz",
    "fraunces": "https://registry.npmjs.org/@fontsource-variable/fraunces/-/fraunces-5.3.0.tgz",
}
FONT_FILES = {
    # key: (package, path inside tarball, variable-font instance or None)
    "sans": ("geist", "package/dist/fonts/geist-sans/Geist-Regular.woff2", None),
    "sans-md": ("geist", "package/dist/fonts/geist-sans/Geist-Medium.woff2", None),
    "sans-sb": ("geist", "package/dist/fonts/geist-sans/Geist-SemiBold.woff2", None),
    "mono": ("geist", "package/dist/fonts/geist-mono/GeistMono-Regular.woff2", None),
    "serif": ("fraunces", "package/files/fraunces-latin-full-normal.woff2",
              {"opsz": 72, "wght": 400, "SOFT": 0, "WONK": 0}),
}
CSS_FAMILY = {
    "sans": ("GS", 400), "sans-md": ("GS", 500), "sans-sb": ("GS", 600),
    "mono": ("GM", 400), "serif": ("FR", 400),
}
FALLBACK = {
    "GS": "ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif",
    "GM": "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",
    "FR": "Georgia,'Times New Roman',serif",
}


def _package(name: str) -> tarfile.TarFile:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{name}.tgz"
    if not path.exists():
        print(f"fetching {PACKAGES[name]}")
        with urllib.request.urlopen(PACKAGES[name]) as r:
            path.write_bytes(r.read())
    return tarfile.open(path)


class Font:
    def __init__(self, key: str):
        pkg, member, inst = FONT_FILES[key]
        raw = _package(pkg).extractfile(member).read()
        font = TTFont(io.BytesIO(raw))
        if inst:
            font = instancer.instantiateVariableFont(font, inst)
        buf = io.BytesIO()
        font.flavor = None
        font.save(buf)
        self.data = buf.getvalue()
        self.font = TTFont(io.BytesIO(self.data))
        self.cmap = self.font.getBestCmap()
        self.hmtx = self.font["hmtx"]
        self.upm = self.font["head"].unitsPerEm

    def advance(self, ch: str, size: float) -> float:
        g = self.cmap.get(ord(ch))
        if g is None:
            raise ValueError(f"glyph {ch!r} missing")
        return self.hmtx[g][0] * size / self.upm

    def width(self, s: str, size: float, tracking: float = 0) -> float:
        return sum(self.advance(c, size) for c in s) + tracking * max(len(s) - 1, 0)

    def subset_woff2(self, chars: str) -> bytes:
        opts = subset.Options()
        opts.flavor = "woff2"
        opts.layout_features = ["kern"]
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


def wrap(text: str, font: str, size: float, max_w: float) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if FONTS[font].width(trial, size) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ------------------------------------------------------------- svg builder ---
def f(n: float) -> str:
    s = f"{n:.2f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


class Svg:
    def __init__(self, w: int, h: int, label: str, title: str, prefix: str):
        self.w, self.h, self.label, self.title = w, h, label, title
        self.prefix = prefix
        self.body: list[str] = []
        self.css: list[str] = []
        self.defs: list[str] = []
        self.chars: dict[str, set[str]] = {}
        self.n = 0

    # -- elements
    def add(self, s: str) -> None:
        self.body.append(s)

    def text(self, x, y, s, font="sans", size=12, fill=TEXT, anchor="start",
             attrs="", tracking=0) -> str:
        self.chars.setdefault(font, set()).update(s)
        fam, wt = CSS_FAMILY[font]
        a = f' text-anchor="{anchor}"' if anchor != "start" else ""
        t = f' letter-spacing="{f(tracking)}"' if tracking else ""
        return (f'<text x="{f(x)}" y="{f(y)}" class="{fam.lower()}" font-weight="{wt}" '
                f'font-size="{f(size)}" fill="{fill}"{a}{t} {attrs}>{escape(s)}</text>')

    # -- animation
    def anim(self, frames, *, iterations="1", delay=0.0, total=None, fill="both",
             direction="normal") -> str:
        """frames: [(seconds, 'css decls', easing-or-None), ...] -> class name"""
        self.n += 1
        name = f"{self.prefix}{self.n}"
        total = total if total is not None else frames[-1][0]
        parts = []
        for t, decl, ease in frames:
            p = 0 if total == 0 else t / total * 100
            e = f"animation-timing-function:{ease}" if ease else ""
            parts.append(f"{f(round(p, 3))}%{{{decl};{e}}}")
        self.css.append(f"@keyframes {name}{{{''.join(parts)}}}")
        self.css.append(
            f".{name}{{animation:{name} {f(total)}s linear {f(delay)}s {iterations} {direction} {fill}}}")
        return name

    # -- output
    def render(self) -> str:
        faces = []
        for key, chars in sorted(self.chars.items()):
            fam, wt = CSS_FAMILY[key]
            data = base64.b64encode(FONTS[key].subset_woff2("".join(sorted(chars)) + " ")).decode()
            faces.append(f"@font-face{{font-family:{fam};font-weight:{wt};"
                         f"src:url(data:font/woff2;base64,{data}) format('woff2')}}")
        fams = "".join(f".{k.lower()}{{font-family:{k},{v}}}" for k, v in FALLBACK.items())
        css = "".join(faces) + fams + "".join(self.css) + (
            "@media (prefers-reduced-motion:reduce){*{animation:none!important}}")
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-label="{escape(self.label)}" fill="none">'
            f"<title>{escape(self.title)}</title><defs>{''.join(self.defs)}</defs>"
            f"<style>{css}</style>{''.join(self.body)}</svg>")

    def save(self, name: str) -> None:
        OUT.mkdir(exist_ok=True)
        data = self.render()
        (OUT / name).write_text(data, encoding="utf-8")
        print(f"{name:28s} {len(data) / 1024:6.1f} KB")


# ------------------------------------------------------------------ shapes ---
def cursor(color: str, name: str, svg: Svg, label_font="sans-md") -> str:
    """Multiplayer cursor with a name tag. Tip at (0,0)."""
    w = FONTS[label_font].width(name, 11) + 14
    return (
        f'<path d="M0 0 L0 15.5 L4.3 11.6 L7.2 18.2 L9.9 17 L7.1 10.6 L12.8 10.6 Z" '
        f'fill="{color}" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/>'
        f'<rect x="10" y="17" width="{f(w)}" height="19" rx="6" fill="{color}"/>'
        + svg.text(17, 30.5, name, label_font, 11, "#fff"))


def sparkle(cx, cy, r, color, stroke=None) -> str:
    """Four-point star."""
    k = r * 0.28
    d = (f"M{f(cx)} {f(cy - r)} C{f(cx + k * .4)} {f(cy - k)} {f(cx + k)} {f(cy - k * .4)} {f(cx + r)} {f(cy)} "
         f"C{f(cx + k)} {f(cy + k * .4)} {f(cx + k * .4)} {f(cy + k)} {f(cx)} {f(cy + r)} "
         f"C{f(cx - k * .4)} {f(cy + k)} {f(cx - k)} {f(cy + k * .4)} {f(cx - r)} {f(cy)} "
         f"C{f(cx - k)} {f(cy - k * .4)} {f(cx - k * .4)} {f(cy - k)} {f(cx)} {f(cy - r)}Z")
    return f'<path d="{d}" fill="{color}"/>'


def claude_mark(cx, cy, r, color="#fff") -> str:
    """Radiating-burst glyph used for Claude's avatar."""
    out = []
    for i in range(8):
        a = i * math.pi / 4 + math.pi / 8
        out.append(f'<line x1="{f(cx + math.cos(a) * r * .28)}" y1="{f(cy + math.sin(a) * r * .28)}" '
                   f'x2="{f(cx + math.cos(a) * r)}" y2="{f(cy + math.sin(a) * r)}"/>')
    return f'<g stroke="{color}" stroke-width="1.6" stroke-linecap="round">{"".join(out)}</g>'


def bezier(p0, p1, p2, p3, u):
    a = (1 - u) ** 3
    b = 3 * (1 - u) ** 2 * u
    c = 3 * (1 - u) * u ** 2
    d = u ** 3
    return (a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
            a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1])


def arc_samples(p0, p1, p2, p3, n):
    """n+1 points at equal arc-length steps, plus total length."""
    fine = [bezier(p0, p1, p2, p3, i / 400) for i in range(401)]
    acc = [0.0]
    for a, b in zip(fine, fine[1:]):
        acc.append(acc[-1] + math.dist(a, b))
    total = acc[-1]
    out, j = [], 0
    for k in range(n + 1):
        target = total * k / n
        while j < 400 and acc[j + 1] < target:
            j += 1
        out.append(fine[min(j + 1, 400)] if k else fine[0])
    return out, total


EASE = "cubic-bezier(.45,0,.2,1)"
OUT_EXPO = "cubic-bezier(.16,1,.3,1)"


def card(svg: Svg, w: int, h: int, vis_h: int, clip_id: str) -> None:
    svg.defs.append(f'<clipPath id="{clip_id}"><rect x="5" y="5" width="{w - 10}" height="{vis_h}" rx="12"/></clipPath>')
    svg.add(f'<rect x="5.5" y="5.5" width="{w - 11}" height="{h - 11}" rx="12" fill="{PANEL}" stroke="{LINE}"/>')


def card_text(svg: Svg, w, vis_h, title, meta, sub, link, big):
    ts, ss = (16, 12.5) if big else (13.5, 11.5)
    ty = 5 + vis_h + (29 if big else 25)
    sy = ty + (21 if big else 18)
    svg.add(svg.text(21 if big else 17, ty, title, "sans-sb", ts, TEXT))
    svg.add(svg.text(w - (21 if big else 17), ty, meta, "mono", 11 if big else 10, TEXT_2, "end"))
    svg.add(svg.text(21 if big else 17, sy, sub, "sans", ss, TEXT_3))
    if link:
        svg.add(svg.text(w - (21 if big else 17), sy, link, "mono", 11 if big else 10, TEXT_3, "end"))


def dot_grid(svg: Svg, pid: str, step=16, color="#1C1C21", r=0.9) -> None:
    svg.defs.append(
        f'<pattern id="{pid}" width="{step}" height="{step}" patternUnits="userSpaceOnUse">'
        f'<circle cx="{step / 2}" cy="{step / 2}" r="{r}" fill="{color}"/></pattern>')


# ==================================================================== HERO ===
def hero() -> None:
    W, H = 846, 440
    s = Svg(W, H, "A design tool canvas. Rishi's cursor draws a frame and types 'Rishi Ashar', "
            "then Claude's cursor joins and draws an ease-out-expo motion curve that animates a dot "
            "with a little UI sound. Inspector: experience designer in Toronto prototyping with AI. "
            "Stack: TypeScript, React, Next.js, Framer Motion, WebGL, Web Audio.",
            "Rishi Ashar", "h")
    LP, RP = 176, 584              # left panel right edge, right panel left edge
    TB = 40                        # title bar height
    s.defs.append(f'<clipPath id="win"><rect width="{W}" height="{H}" rx="12"/></clipPath>')
    s.defs.append(f'<clipPath id="cv"><rect x="{LP + 1}" y="{TB + 1}" width="{RP - LP - 1}" height="{H - TB - 1}"/></clipPath>')
    dot_grid(s, "grid")
    s.defs.append('<filter id="sh" x="-20%" y="-20%" width="140%" height="150%">'
                  '<feDropShadow dx="0" dy="10" stdDeviation="14" flood-color="#000" flood-opacity=".55"/></filter>')

    s.add('<g clip-path="url(#win)">')
    s.add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')

    # ---- canvas -------------------------------------------------------------
    s.add(f'<rect x="{LP}" y="{TB}" width="{RP - LP}" height="{H - TB}" fill="url(#grid)"/>')

    fx, fy, fw, fh = 220, 94, 320, 212
    T_DRAG0, T_DRAG1 = 0.9, 1.7
    T_TEXT = 2.3
    T_SUB = 3.45
    T_CL_IN, T_CL_DRAW0, T_CL_DRAW1 = 3.7, 4.3, 5.2
    T_PLAY = 6.1
    T_IDLE = 6.4

    # frame (grows from top-left)
    sx = s.anim([(0, "transform:scale(0,0);opacity:0", None), (T_DRAG0, "transform:scale(0,0);opacity:1", EASE),
                 (T_DRAG1, "transform:scale(1,1);opacity:1", None)])
    s.add(f'<g transform="translate({fx} {fy})"><g class="{sx}">'
          f'<rect width="{fw}" height="{fh}" rx="2" fill="#111114" stroke="{LINE_2}" vector-effect="non-scaling-stroke" filter="url(#sh)"/>'
          f'</g></g>')
    lbl = s.anim([(0, "opacity:0", None), (T_DRAG0 + .1, "opacity:0", None), (T_DRAG0 + .3, "opacity:1", None)])
    s.add(f'<g class="{lbl}">' + s.text(fx, fy - 8, "Hero", "sans-md", 11, TEXT_3) + "</g>")

    # name, typed one glyph at a time
    name = "Rishi Ashar"
    nx, ny, nsize = fx + 24, fy + 64, 42
    x = nx
    char_times = []
    for i, ch in enumerate(name):
        t = T_TEXT + 0.05 + i * 0.085
        char_times.append((t, x))
        if ch != " ":
            c = s.anim([(0, "opacity:0", "steps(1,end)"), (t, "opacity:1", None)])
            s.add(f'<g class="{c}">' + s.text(x, ny, ch, "serif", nsize, "#F4F4F5") + "</g>")
        x += FONTS["serif"].advance(ch, nsize)
    name_end = x
    # caret
    caret_frames = [(0, f"opacity:0;transform:translateX({f(nx)}px)", "steps(1,end)"),
                    (T_TEXT, f"opacity:1;transform:translateX({f(nx)}px)", "steps(1,end)")]
    xs = [cx for _, cx in char_times[1:]] + [name_end]
    for (t, _), cx in zip(char_times, xs):
        caret_frames.append((t + .001, f"opacity:1;transform:translateX({f(cx)}px)", "steps(1,end)"))
    tt = char_times[-1][0] + .25
    caret_frames += [(tt, f"opacity:0;transform:translateX({f(name_end)}px)", "steps(1,end)"),
                     (tt + .25, f"opacity:1;transform:translateX({f(name_end)}px)", "steps(1,end)"),
                     (T_SUB, f"opacity:0;transform:translateX({f(name_end)}px)", None)]
    c = s.anim(caret_frames)
    s.add(f'<g class="{c}" style="opacity:0"><rect x="0" y="{ny - 33}" width="2" height="42" fill="{BLUE}"/></g>')

    sub = s.anim([(0, "opacity:0;transform:translateY(5px)", None), (T_SUB, "opacity:0;transform:translateY(5px)", EASE),
                  (T_SUB + .45, "opacity:1;transform:translateY(0)", None)])
    s.add(f'<g class="{sub}">' + s.text(nx + 1, fy + 92, "Experience designer · Toronto", "sans", 13.5, TEXT_2) + "</g>")

    # easing editor
    bx, by, bw, bh = fx + 24, fy + 114, 120, 74
    p0 = (bx + 14, by + bh - 14)
    p3 = (bx + bw - 14, by + 14)
    dx, dy = p3[0] - p0[0], p3[1] - p0[1]
    p1 = (p0[0] + .16 * dx, p0[1] + 1 * dy)
    p2 = (p0[0] + .30 * dx, p0[1] + 1 * dy)
    box = s.anim([(0, "opacity:0", None), (T_CL_DRAW0 - .15, "opacity:0", None), (T_CL_DRAW0 + .1, "opacity:1", None)])
    s.add(f'<g class="{box}"><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="8" fill="#0D0D10" stroke="{LINE_2}"/>'
          f'<path d="M{f(p0[0])} {f(by + 8)} V{f(p0[1])} H{f(bx + bw - 8)}" stroke="#25252B" stroke-dasharray="2 3"/></g>')
    pts, plen = arc_samples(p0, p1, p2, p3, 14)
    curve = s.anim([(0, f"stroke-dashoffset:{f(plen)}", None), (T_CL_DRAW0, f"stroke-dashoffset:{f(plen)}", None),
                    (T_CL_DRAW1, "stroke-dashoffset:0", None)])
    s.add(f'<path class="{curve}" d="M{f(p0[0])} {f(p0[1])} C{f(p1[0])} {f(p1[1])} {f(p2[0])} {f(p2[1])} {f(p3[0])} {f(p3[1])}" '
          f'stroke="#F4F4F5" stroke-width="2" stroke-linecap="round" stroke-dasharray="{f(plen)}"/>')
    hd = s.anim([(0, "opacity:0", None), (T_CL_DRAW1, "opacity:0", EASE), (T_CL_DRAW1 + .3, "opacity:1", None)])
    s.add(f'<g class="{hd}" stroke="{BLUE}">'
          f'<line x1="{f(p0[0])}" y1="{f(p0[1])}" x2="{f(p1[0])}" y2="{f(p1[1])}"/>'
          f'<line x1="{f(p3[0])}" y1="{f(p3[1])}" x2="{f(p2[0])}" y2="{f(p2[1])}"/>'
          f'<circle cx="{f(p1[0])}" cy="{f(p1[1])}" r="3.5" fill="{BG}" stroke-width="1.5"/>'
          f'<circle cx="{f(p2[0])}" cy="{f(p2[1])}" r="3.5" fill="{BG}" stroke-width="1.5"/></g>')
    s.add(f'<circle cx="{f(p0[0])}" cy="{f(p0[1])}" r="2.5" fill="#F4F4F5" class="{box}"/>')
    s.add(f'<circle cx="{f(p3[0])}" cy="{f(p3[1])}" r="2.5" fill="#F4F4F5" class="{hd}"/>')

    # motion preview: track, dot, ripple, waveform
    tx0, tx1, ty = fx + 172, fx + 294, by + bh / 2 + 6
    L = tx1 - tx0
    show = s.anim([(0, "opacity:0", None), (T_PLAY - .45, "opacity:0", EASE), (T_PLAY - .15, "opacity:1", None)])
    s.add(f'<g class="{show}">'
          f'<line x1="{tx0}" y1="{f(ty)}" x2="{tx1}" y2="{f(ty)}" stroke="#26262C" stroke-width="2" stroke-linecap="round"/>'
          + s.text(tx0 - 7, by + bh - 1, "out-expo · 600ms", "mono", 10, TEXT_3) + "</g>")
    P = 2.4
    loop = [(0, "transform:translateX(0)", OUT_EXPO), (P * .25, f"transform:translateX({L}px)", None),
            (P * .5, f"transform:translateX({L}px)", OUT_EXPO), (P * .75, "transform:translateX(0)", None),
            (P, "transform:translateX(0)", None)]
    dot = s.anim(loop, iterations="infinite", delay=T_PLAY)
    for end_x, at in ((tx1, .25), (tx0, .75)):
        rp = s.anim([(0, "transform:scale(.4);opacity:0", "steps(1,end)"), (P * at, "transform:scale(.4);opacity:.7", "ease-out"),
                     (P * at + .55, "transform:scale(2.6);opacity:0", None), (P, "transform:scale(2.6);opacity:0", None)],
                    iterations="infinite", delay=T_PLAY)
        s.add(f'<g transform="translate({end_x} {f(ty)})"><circle class="{rp}" r="7" stroke="{RISHI}" stroke-width="1.5" style="opacity:0"/></g>')
    s.add(f'<g class="{show}"><g transform="translate({tx0} {f(ty)})"><g class="{dot}">'
          f'<circle r="11" fill="{RISHI}" opacity=".18"/><circle r="6.5" fill="{RISHI}"/><circle r="2.2" fill="#fff"/></g></g></g>')
    heights = [5, 9, 14, 8, 12, 6, 10, 4]
    wv_x0, wv_y = tx1 - (len(heights) - 1) * 6, by + 14
    for i, hgt in enumerate(heights):
        k = .25 + i * .012
        frames = [(0, "transform:scaleY(.35)", None), (P * (k - .01), "transform:scaleY(.35)", "ease-out"),
                  (P * k + .06, "transform:scaleY(1)", "ease-in"), (P * k + .45, "transform:scaleY(.35)", None),
                  (P * (k + .49), "transform:scaleY(.35)", "ease-out"), (P * (k + .5) + .06, "transform:scaleY(1)", "ease-in"),
                  (P * (k + .5) + .45, "transform:scaleY(.35)", None), (P, "transform:scaleY(.35)", None)]
        frames.sort(key=lambda fr: fr[0])
        bar = s.anim(frames, iterations="infinite", delay=T_PLAY)
        s.add(f'<g class="{show}"><g transform="translate({f(wv_x0 + i * 6)} {f(wv_y)})"><g class="{bar}">'
              f'<rect x="-1.25" y="{-hgt / 2}" width="2.5" height="{hgt}" rx="1.25" fill="{RISHI}" opacity=".85"/></g></g></g>')

    # Rishi's selection on the frame (blue) + handles + size badge
    sel = s.anim([(0, "opacity:0", "steps(1,end)"), (T_DRAG0, "opacity:1", "steps(1,end)"), (T_TEXT - .05, "opacity:0", None)])
    hs = 7
    s.add(f'<g class="{sel}" style="opacity:0">')
    s.add(f'<rect x="{fx}" y="{fy}" width="{fw}" height="{fh}" stroke="{BLUE}" stroke-width="1.5">'
          f'<animate attributeName="width" values="0;0;{fw}" keyTimes="0;{f(T_DRAG0 / T_DRAG1)};1" dur="{T_DRAG1}s" '
          f'calcMode="spline" keySplines="0 0 1 1;.45 0 .2 1" fill="freeze"/>'
          f'<animate attributeName="height" values="0;0;{fh}" keyTimes="0;{f(T_DRAG0 / T_DRAG1)};1" dur="{T_DRAG1}s" '
          f'calcMode="spline" keySplines="0 0 1 1;.45 0 .2 1" fill="freeze"/></rect>')
    for ox, oy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        anims = ""
        if ox:
            anims += (f'<animate attributeName="x" values="{fx - hs / 2};{fx - hs / 2};{fx + fw - hs / 2}" '
                      f'keyTimes="0;{f(T_DRAG0 / T_DRAG1)};1" dur="{T_DRAG1}s" calcMode="spline" keySplines="0 0 1 1;.45 0 .2 1" fill="freeze"/>')
        if oy:
            anims += (f'<animate attributeName="y" values="{fy - hs / 2};{fy - hs / 2};{fy + fh - hs / 2}" '
                      f'keyTimes="0;{f(T_DRAG0 / T_DRAG1)};1" dur="{T_DRAG1}s" calcMode="spline" keySplines="0 0 1 1;.45 0 .2 1" fill="freeze"/>')
        s.add(f'<rect x="{f(fx + ox * fw - hs / 2)}" y="{f(fy + oy * fh - hs / 2)}" width="{hs}" height="{hs}" rx="1.5" '
              f'fill="#fff" stroke="{BLUE}" stroke-width="1.5">{anims}</rect>')
    s.add("</g>")
    badge = s.anim([(0, "opacity:0", None), (T_DRAG1 - .05, "opacity:0", None), (T_DRAG1 + .1, "opacity:1", "steps(1,end)"),
                    (T_TEXT - .05, "opacity:0", None)])
    bt = f"{fw} × {fh}"
    bw_ = FONTS["sans-md"].width(bt, 10.5) + 12
    s.add(f'<g class="{badge}" style="opacity:0"><rect x="{f(fx + fw / 2 - bw_ / 2)}" y="{fy + fh + 8}" width="{f(bw_)}" height="18" rx="4" fill="{BLUE}"/>'
          + s.text(fx + fw / 2, fy + fh + 20.5, bt, "sans-md", 10.5, "#fff", "middle") + "</g>")

    # Claude's selection on the easing box (coral, like a collaborator's selection)
    csel = s.anim([(0, "opacity:0", "steps(1,end)"), (T_CL_DRAW0, "opacity:1", "steps(1,end)"), (T_CL_DRAW1 + .5, "opacity:0", None)])
    s.add(f'<g class="{csel}" style="opacity:0"><rect x="{bx - 3}" y="{by - 3}" width="{bw + 6}" height="{bh + 6}" rx="10" stroke="{CLAUDE}" stroke-width="1.5"/></g>')

    # floating toolbar
    tools = ["move", "frame", "pen", "text", "comment"]
    tb_w = len(tools) * 32 + 8
    tb_x, tb_y = (LP + RP) / 2 - tb_w / 2, H - 52
    s.add(f'<rect x="{f(tb_x)}" y="{tb_y}" width="{tb_w}" height="38" rx="11" fill="#151518" stroke="{LINE_2}" filter="url(#sh)"/>')
    active = [(0, 0), (.35, 1), (1.85, 3), (T_SUB, 0)]
    fr = []
    for t, idx in active:
        fr.append((t, f"transform:translateX({idx * 32}px)", "steps(1,end)" if t else "steps(1,end)"))
    fr.append((T_SUB + .01, "transform:translateX(0)", None))
    hl = s.anim(fr)
    s.add(f'<g transform="translate({f(tb_x + 5)} {tb_y + 5})"><rect class="{hl}" width="30" height="28" rx="7" fill="{BLUE}"/></g>')
    icons = {
        "move": '<path d="M-4 -6 L-4 5 L-1 2.2 L1.2 7 L3 6.2 L.9 1.6 L4.8 1.6 Z" fill="#fff" stroke="#fff" stroke-width=".6" stroke-linejoin="round"/>',
        "frame": '<path d="M-3 -7 V7 M3 -7 V7 M-7 -3 H7 M-7 3 H7" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>',
        "pen": '<path d="M-6 5 C-4 -5 4 -5 6 5" stroke="#fff" stroke-width="1.4"/><rect x="-7.5" y="3.5" width="3" height="3" fill="#151518" stroke="#fff"/><rect x="4.5" y="3.5" width="3" height="3" fill="#151518" stroke="#fff"/>',
        "text": '<path d="M-5 -6 H5 M0 -6 V6.5" stroke="#fff" stroke-width="1.5" stroke-linecap="round"/>',
        "comment": '<path d="M-6 -5.5 H6 Q7 -5.5 7 -4.5 V3 Q7 4 6 4 H-1 L-5 7 V4 H-6 Q-7 4 -7 3 V-4.5 Q-7 -5.5 -6 -5.5Z" stroke="#fff" stroke-width="1.3" stroke-linejoin="round"/>',
    }
    for i, tname in enumerate(tools):
        s.add(f'<g transform="translate({f(tb_x + 5 + i * 32 + 15)} {tb_y + 19})" opacity=".92">{icons[tname]}</g>')

    # ---- cursors (clipped to the canvas) -----------------------------------
    s.add('<g clip-path="url(#cv)">')

    def path_anim(points, float_delay, amp):
        frames = [(t, f"transform:translate({f(x)}px,{f(y)}px)", e) for t, (x, y), e in points]
        outer = s.anim(frames)
        fl = s.anim([(0, "transform:translate(0,0)", "ease-in-out"), (2.6, f"transform:translate({amp[0]}px,{amp[1]}px)", "ease-in-out"),
                     (5.2, "transform:translate(0,0)", None)], iterations="infinite", delay=float_delay)
        last = points[-1][1]
        return outer, fl, last

    rishi_pts = [
        (0, (512, 330), None), (.35, (512, 330), EASE), (T_DRAG0, (fx, fy), EASE),
        (T_DRAG1, (fx + fw, fy + fh), None), (1.85, (fx + fw, fy + fh), EASE),
        (2.25, (nx - 2, ny - 30), None), (2.4, (nx - 2, ny - 30), EASE), (3.0, (fx + 282, fy + 48), None),
        (5.45, (fx + 282, fy + 48), EASE), (5.95, (tx0 + 2, ty + 2), None), (T_PLAY + .15, (tx0 + 2, ty + 2), EASE),
        (T_IDLE + .6, (fx + fw - 52, fy + fh + 34), None),
    ]
    o, fl, (lx, ly) = path_anim(rishi_pts, T_IDLE + .6, (-5, 3))
    s.add(f'<g class="{o}" style="transform:translate({lx}px,{ly}px)"><g class="{fl}">{cursor(RISHI, "Rishi", s)}</g></g>')

    claude_pts = [(0, (620, 300), None), (T_CL_IN, (620, 300), EASE), (T_CL_DRAW0, pts[0], None)]
    for k, pnt in enumerate(pts[1:], 1):
        claude_pts.append((T_CL_DRAW0 + (T_CL_DRAW1 - T_CL_DRAW0) * k / (len(pts) - 1), pnt, None))
    claude_pts += [(T_CL_DRAW1 + .2, pts[-1], EASE), (T_IDLE + .3, (fx - 22, fy + fh + 30), None)]
    o, fl, (lx, ly) = path_anim(claude_pts, T_IDLE + .3, (4, -4))
    s.add(f'<g class="{o}" style="transform:translate({f(lx)}px,{f(ly)}px)"><g class="{fl}">')
    # name tag swaps to a cursor-chat bubble for a moment
    chat = "feels right"
    tag = s.anim([(0, "opacity:1", None), (T_CL_DRAW1 + .15, "opacity:1", None), (T_CL_DRAW1 + .3, "opacity:0", None),
                  (T_IDLE + 1.6, "opacity:0", None), (T_IDLE + 1.8, "opacity:1", None)])
    bub = s.anim([(0, "opacity:0;transform:scale(.6)", None), (T_CL_DRAW1 + .15, "opacity:0;transform:scale(.6)", OUT_EXPO),
                  (T_CL_DRAW1 + .5, "opacity:1;transform:scale(1)", None), (T_IDLE + 1.6, "opacity:1;transform:scale(1)", None),
                  (T_IDLE + 1.8, "opacity:0;transform:scale(.9)", None)])
    cw = FONTS["sans"].width(chat, 12) + 22
    s.add(f'<path d="M0 0 L0 15.5 L4.3 11.6 L7.2 18.2 L9.9 17 L7.1 10.6 L12.8 10.6 Z" fill="{CLAUDE}" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/>')
    s.add(f'<g class="{tag}"><rect x="10" y="17" width="{f(FONTS["sans-md"].width("Claude", 11) + 14)}" height="19" rx="6" fill="{CLAUDE}"/>'
          + s.text(17, 30.5, "Claude", "sans-md", 11, "#fff") + "</g>")
    s.add(f'<g transform="translate(10 17)"><g class="{bub}" style="opacity:0"><rect width="{f(cw)}" height="26" rx="13" fill="{CLAUDE}"/>'
          + s.text(11, 17.5, chat, "sans", 12, "#fff") + "</g></g>")
    s.add("</g></g>")
    s.add("</g>")  # /clip canvas

    # ---- title bar -----------------------------------------------------------
    s.add(f'<rect width="{W}" height="{TB}" fill="{PANEL}"/><line x1="0" y1="{TB - .5}" x2="{W}" y2="{TB - .5}" stroke="{LINE}"/>')
    for i in range(3):
        s.add(f'<circle cx="{18 + i * 16}" cy="20" r="5" fill="#2A2A2F"/>')
    a, b = "rishiashar", " / Profile"
    wa, wb = FONTS["sans-md"].width(a, 12), FONTS["sans"].width(b, 12)
    x0 = W / 2 - (wa + wb) / 2
    s.add(s.text(x0, 24.5, a, "sans-md", 12, TEXT) + s.text(x0 + wa, 24.5, b, "sans", 12, TEXT_3))
    s.add(f'<rect x="{W - 66}" y="9" width="54" height="22" rx="6" fill="{BLUE}"/>' + s.text(W - 39, 24, "Share", "sans-md", 11.5, "#fff", "middle"))
    av = s.anim([(0, "opacity:0;transform:scale(.4)", None), (T_CL_IN, "opacity:0;transform:scale(.4)", OUT_EXPO),
                 (T_CL_IN + .5, "opacity:1;transform:scale(1)", None)])
    s.add(f'<g transform="translate({W - 104} 20)"><g class="{av}"><circle r="11" fill="{CLAUDE}" stroke="{PANEL}" stroke-width="2"/>'
          f'{claude_mark(0, 0, 6.5)}</g></g>')
    s.add(f'<g transform="translate({W - 86} 20)"><circle r="11" fill="{RISHI}" stroke="{PANEL}" stroke-width="2"/>'
          + s.text(0, 4, "R", "sans-sb", 11, "#fff", "middle") + "</g>")
    s.add(s.text(W - 124, 24, "100%", "mono", 10.5, TEXT_3, "end"))

    # ---- left panel ----------------------------------------------------------
    s.add(f'<rect x="0" y="{TB}" width="{LP}" height="{H - TB}" fill="{PANEL}"/><line x1="{LP + .5}" y1="{TB}" x2="{LP + .5}" y2="{H}" stroke="{LINE}"/>')
    s.add(s.text(16, 66, "Pages", "sans-sb", 11.5, TEXT))
    for i, pg in enumerate(["Profile", "Work", "Play"]):
        y = 78 + i * 24
        if i == 0:
            s.add(f'<rect x="8" y="{y}" width="{LP - 16}" height="22" rx="5" fill="#1B1B1F"/>')
        s.add(s.text(28, y + 15, pg, "sans", 11.5, TEXT if i == 0 else TEXT_2))
        if i == 0:
            s.add(f'<path d="M14 {y + 11} l2.5 2.5 l4.5 -5" stroke="{TEXT}" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>')
    s.add(f'<line x1="0" y1="160.5" x2="{LP}" y2="160.5" stroke="{LINE}"/>')
    s.add(s.text(16, 184, "Layers", "sans-sb", 11.5, TEXT))
    layer_icon = {
        "frame": '<path d="M-2.5 -5 V5 M2.5 -5 V5 M-5 -2.5 H5 M-5 2.5 H5" stroke="{c}" stroke-width="1.1" stroke-linecap="round"/>',
        "text": '<path d="M-4 -4.5 H4 M0 -4.5 V5" stroke="{c}" stroke-width="1.2" stroke-linecap="round"/>',
        "vector": '<path d="M-4.5 4 C-3 -4 3 -4 4.5 4" stroke="{c}" stroke-width="1.1"/><circle cx="-4.5" cy="4" r="1.3" fill="{c}"/><circle cx="4.5" cy="4" r="1.3" fill="{c}"/>',
        "sound": '<path d="M-4.5 0 V0 M-1.5 -3 V3 M1.5 -4.5 V4.5 M4.5 -2 V2" stroke="{c}" stroke-width="1.3" stroke-linecap="round"/>',
    }
    layers = [("frame", "Hero", 0, .95), ("text", "Rishi Ashar", 1, T_TEXT), ("text", "Experience designer", 1, T_SUB),
              ("vector", "motion/out-expo", 1, T_CL_DRAW0), ("sound", "ui/tick", 1, T_PLAY - .2)]
    ly0 = 196
    sel_rows = [(0, 0, .95), (1, T_TEXT, None), (2, T_SUB, None), (4, T_PLAY - .2, None)]
    fr = [(0, f"opacity:0;transform:translateY({ly0}px)", "steps(1,end)"), (.95, f"opacity:1;transform:translateY({ly0}px)", "steps(1,end)")]
    for row, t, _ in sel_rows[1:]:
        fr.append((t, f"opacity:1;transform:translateY({ly0 + row * 25}px)", "steps(1,end)"))
    fr.append((T_IDLE, f"opacity:0;transform:translateY({ly0 + 4 * 25}px)", None))
    hl = s.anim(fr)
    s.add(f'<rect class="{hl}" style="opacity:0" x="8" y="0" width="{LP - 16}" height="23" rx="5" fill="{BLUE}" fill-opacity=".16"/>')
    for i, (kind, label, indent, t) in enumerate(layers):
        y = ly0 + i * 25
        ap = s.anim([(0, "opacity:0;transform:translateX(-6px)", None), (t, "opacity:0;transform:translateX(-6px)", OUT_EXPO),
                     (t + .4, "opacity:1;transform:translateX(0)", None)])
        ix = 20 + indent * 14
        s.add(f'<g class="{ap}"><g transform="translate({ix} {y + 11.5})">{layer_icon[kind].format(c=TEXT_2)}</g>'
              + s.text(ix + 12, y + 15.5, label, "sans", 11.5, TEXT if i == 0 else TEXT_2) + "</g>")
    s.add(s.text(16, H - 18, "43.65°N 79.38°W", "mono", 10, TEXT_3))

    # ---- right panel (inspector) ---------------------------------------------
    s.add(f'<rect x="{RP}" y="{TB}" width="{W - RP}" height="{H - TB}" fill="{PANEL}"/><line x1="{RP - .5}" y1="{TB}" x2="{RP - .5}" y2="{H}" stroke="{LINE}"/>')
    ix = RP + 18
    s.add(s.text(ix, 66, "Design", "sans-sb", 11.5, TEXT))
    s.add(s.text(ix + FONTS["sans-sb"].width("Design", 11.5) + 16, 66, "Prototype", "sans-md", 11.5, TEXT_3))
    s.add(f'<line x1="{RP}" y1="80.5" x2="{W}" y2="80.5" stroke="{LINE}"/>')
    sections = [
        ("About", "sans", 12, ["Experience designer in Toronto,", "designing and prototyping ideas", "with AI and emerging tools."]),
        ("Stack", "mono", 11, ["TypeScript · React · Next.js", "Framer Motion · WebGL · Web Audio"]),
        ("Tools", "mono", 11, ["Figma · Cursor · Claude Code", "Framer · Codex · Lovable"]),
        ("Elsewhere", "mono", 11, ["rishiashar.com", "x.com/that_rishi07"]),
    ]
    y = 104
    max_w = W - ix - 16
    for i, (head, font, size, lines) in enumerate(sections):
        ap = s.anim([(0, "opacity:0;transform:translateY(4px)", None), (.15 + i * .12, "opacity:0;transform:translateY(4px)", OUT_EXPO),
                     (.6 + i * .12, "opacity:1;transform:translateY(0)", None)])
        s.add(f'<g class="{ap}">' + s.text(ix, y, head, "sans-sb", 11.5, TEXT))
        for j, line in enumerate(lines):
            assert FONTS[font].width(line, size) <= max_w, (line, FONTS[font].width(line, size), max_w)
            s.add(s.text(ix, y + 21 + j * 18, line, font, size, TEXT_2))
        s.add("</g>")
        y += 21 + len(lines) * 18 + 8
        if i < len(sections) - 1:
            s.add(f'<line x1="{RP}" y1="{y + .5}" x2="{W}" y2="{y + .5}" stroke="{LINE}"/>')
            y += 24

    s.add("</g>")  # /clip window
    s.add(f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" stroke="{LINE_2}"/>')
    s.save("hero.svg")




# =================================================================== TILES ===
BIG = (423, 250, 166)     # width, height, visual height
SMALL = (211, 190, 110)


def top_rounded(w, h, r) -> str:
    return f"M0 {h} V{r} Q0 0 {r} 0 H{w - r} Q{w} 0 {w} {r} V{h} Z"


def tile(name, size, title, meta, sub, link, label, draw) -> None:
    w, h, vh = size
    big = size == BIG
    s = Svg(w, h, label, title, name[:2])
    vw = w - 10
    s.defs.append(f'<clipPath id="v"><path d="{top_rounded(vw, vh, 12)}"/></clipPath>')
    s.add(f'<rect x="5.5" y="5.5" width="{w - 11}" height="{h - 11}" rx="12" fill="{PANEL}"/>')
    s.add(f'<g transform="translate(5 5)"><g clip-path="url(#v)"><rect width="{vw}" height="{vh}" fill="#0C0C0F"/>')
    draw(s, vw, vh)
    s.add(f'</g><line x1="0" y1="{vh + .5}" x2="{vw}" y2="{vh + .5}" stroke="{LINE}"/></g>')
    s.add(f'<rect x="5.5" y="5.5" width="{w - 11}" height="{h - 11}" rx="12" stroke="{LINE}"/>')
    # text block, with overlap checks
    pad = 21 if big else 17
    ts, ss, ms = (16, 12.5, 11) if big else (13.5, 11.5, 10)
    ty = 5 + vh + (30 if big else 26)
    sy = ty + (21 if big else 18)
    room = w - 2 * pad
    assert FONTS["sans-sb"].width(title, ts) + FONTS["mono"].width(meta, ms) + 12 <= room, (name, "title row")
    assert FONTS["sans"].width(sub, ss) + (FONTS["mono"].width(link, ms) + 12 if link else 0) <= room, (name, "sub row")
    s.add(s.text(pad, ty, title, "sans-sb", ts, TEXT))
    s.add(s.text(w - pad, ty, meta, "mono", ms, TEXT_2, "end"))
    s.add(s.text(pad, sy, sub, "sans", ss, TEXT_3))
    if link:
        s.add(s.text(w - pad, sy, link, "mono", ms, TEXT_3, "end"))
    s.save(name)


def chip(s: Svg, x, y, text, font="mono", size=10, fill="#16161A", color=TEXT_2, dot=None, anchor="start") -> float:
    tw = FONTS[font].width(text, size)
    w = tw + 16 + (10 if dot else 0)
    x0 = x if anchor == "start" else x - w
    s.add(f'<rect x="{f(x0)}" y="{y}" width="{f(w)}" height="20" rx="10" fill="{fill}" stroke="{LINE}"/>')
    tx = x0 + 8
    if dot:
        s.add(dot(x0 + 11, y + 10))
        tx += 10
    s.add(s.text(tx, y + 13.5, text, font, size, color))
    return w


def pulse_frames(starts, total, scale=(1, 1.45), op=.5, dur=.9):
    fr = [(0, f"transform:scale({scale[0]});opacity:0", None)]
    for st in starts:
        fr.append((st, f"transform:scale({scale[0]});opacity:{op}", "ease-out"))
        fr.append((st + dur, f"transform:scale({scale[1]});opacity:0", None))
    fr.append((total, f"transform:scale({scale[1]});opacity:0", None))
    return fr


# --- ProbeRoom: rehearse interviews with an AI participant --------------------
def draw_proberoom(s: Svg, w, h):
    s.defs.append(f'<radialGradient id="glow" cx="50%" cy="45%" r="60%"><stop offset="0" stop-color="{TEAL}" stop-opacity=".10"/>'
                  f'<stop offset="1" stop-color="{TEAL}" stop-opacity="0"/></radialGradient>')
    s.add(f'<rect width="{w}" height="{h}" fill="url(#glow)"/>')
    T = 6.4
    YOU, AI = (0.0, 2.9), (3.2, 6.1)
    blink = s.anim([(0, "opacity:1", "steps(1,end)"), (.6, "opacity:.25", "steps(1,end)"), (1.2, "opacity:1", None)], iterations="infinite")
    chip(s, 14, 12, "REC 12:48", dot=lambda x, y: f'<circle class="{blink}" cx="{x}" cy="{y}" r="3.2" fill="#EF4444"/>')
    s.add(s.text(w - 14, 25.5, "interview 3 of 5", "mono", 10, TEXT_3, "end"))
    ax, bx, ay, r = 140, w - 140, 68, 24
    for cx, color, win in ((ax, RISHI, YOU), (bx, TEAL, AI)):
        starts = [win[0] + k * .95 for k in range(3)]
        for ring_off in (0, .45):
            ring = s.anim(pulse_frames([st + ring_off for st in starts if st + ring_off + .9 <= win[1] + .6], T),
                          iterations="infinite", total=T)
            s.add(f'<g transform="translate({cx} {ay})"><circle class="{ring}" r="{r}" stroke="{color}" stroke-width="1.5" style="opacity:0"/></g>')
        s.add(f'<circle cx="{cx}" cy="{ay}" r="{r}" fill="#18181D" stroke="{color}" stroke-opacity=".7" stroke-width="1.5"/>')
    s.add(f'<circle cx="{ax}" cy="{ay - 5}" r="6" fill="{TEXT_2}"/><path d="M{ax - 11} {ay + 13} Q{ax} {ay - 1} {ax + 11} {ay + 13}" fill="{TEXT_2}"/>')
    s.add(sparkle(bx, ay, 11, TEAL))
    s.add(s.text(ax, ay + r + 18, "You", "sans-md", 11, TEXT_2, "middle"))
    s.add(s.text(bx, ay + r + 18, "Participant · AI", "sans-md", 11, TEXT_2, "middle"))
    # waveform between the two
    n = 11
    gx0 = (ax + bx) / 2 - (n - 1) * 3.5
    import random
    rnd = random.Random(7)
    for who, color, win in (("you", RISHI, YOU), ("ai", TEAL, AI)):
        vis = s.anim([(0, "opacity:0", None), (win[0], "opacity:0", None), (win[0] + .2, "opacity:1", None),
                      (win[1] - .2, "opacity:1", None), (win[1], "opacity:0", None), (T, "opacity:0", None)], iterations="infinite", total=T)
        s.add(f'<g class="{vis}"' + (' style="opacity:0">' if who == "ai" else '>'))
        for i in range(n):
            amp = 1 - abs(i - (n - 1) / 2) / ((n - 1) / 2) * .6
            fr, t = [], 0
            while t < 1.6:
                fr.append((t, f"transform:scaleY({f(.2 + rnd.random() * .8 * amp)})", "ease-in-out"))
                t += .16 + rnd.random() * .14
            fr.append((1.6, fr[0][1], None))
            bar = s.anim([(round(a, 3), b, c) for a, b, c in fr], iterations="infinite", delay=-rnd.random())
            s.add(f'<g transform="translate({f(gx0 + i * 7)} {ay})"><rect class="{bar}" x="-1.5" y="-12" width="3" height="24" rx="1.5" fill="{color}"/></g>')
        s.add("</g>")
    # captions
    cy0 = 126
    s.add(f'<rect x="20" y="{cy0}" width="{w - 40}" height="26" rx="8" fill="#141418" stroke="{LINE}"/>')
    for (who, color, text), win in ((("YOU", RISHI, "Walk me through the last time you onboarded."), YOU),
                                    (("AI", TEAL, "Honestly? I gave up at the second screen."), AI)):
        assert FONTS["sans"].width(text, 12) < w - 40 - 52 - 14, text
        cap = s.anim([(0, "opacity:0;transform:translateY(3px)", None), (win[0] + .1, "opacity:0;transform:translateY(3px)", OUT_EXPO),
                      (win[0] + .5, "opacity:1;transform:translateY(0)", None), (win[1] - .15, "opacity:1;transform:translateY(0)", None),
                      (win[1] + .1, "opacity:0;transform:translateY(0)", None), (T, "opacity:0", None)], iterations="infinite", total=T)
        s.add(f'<g class="{cap}"' + (' style="opacity:0">' if who == "AI" else '>') + s.text(32, cy0 + 17, who, "mono", 9.5, color, tracking=.6)
              + s.text(70, cy0 + 17.5, text, "sans", 12, TEXT) + "</g>")


# --- Gestura: touchless pinch-gesture canvas ---------------------------------
HAND_OPEN = {
    0: (0, 0), 1: (-14, -10), 2: (-28, -24), 3: (-39, -37), 4: (-48, -48),
    5: (-14, -50), 6: (-17, -72), 7: (-19, -86), 8: (-20, -99),
    9: (0, -53), 10: (0, -78), 11: (0, -93), 12: (0, -106),
    13: (13, -50), 14: (15, -72), 15: (16, -86), 16: (17, -97),
    17: (25, -43), 18: (29, -60), 19: (32, -71), 20: (34, -81),
}
HAND_PINCH = dict(HAND_OPEN)
HAND_PINCH.update({
    2: (-27, -25), 3: (-35, -42), 4: (-35, -58),
    6: (-23, -70), 7: (-32, -73), 8: (-36, -62),
    10: (-3, -77), 11: (-6, -90), 12: (-8, -100),
    14: (13, -71), 15: (12, -84), 16: (11, -94),
    18: (27, -59), 19: (28, -70), 20: (28, -79),
})
BONES = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11), (11, 12),
         (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]


def draw_gestura(s: Svg, w, h):
    dot_grid(s, "g2", 14, "#1A1A1F", .9)
    s.add(f'<rect width="{w}" height="{h}" fill="url(#g2)"/>')
    T = 4.8
    # pose timeline (open=0 / pinch=1) and hand position (A/B)
    poses = [(0, 0), (.4, 0), (.7, 1), (2.1, 1), (2.4, 0), (2.8, 0), (3.1, 1), (4.5, 1), (4.8, 0)]
    A, B = (154, 150), (300, 142)
    pos = [(0, A), (.7, A), (2.1, B), (3.1, B), (4.5, A), (4.8, A)]
    pinch_pt = ((HAND_PINCH[4][0] + HAND_PINCH[8][0]) / 2, (HAND_PINCH[4][1] + HAND_PINCH[8][1]) / 2)

    def move_frames(off=(0, 0)):
        return [(t, f"transform:translate({f(x + off[0])}px,{f(y + off[1])}px)", EASE) for t, (x, y) in pos]

    key_times = ";".join(f(t / T) for t, _ in poses)
    splines = ";".join([".45 0 .2 1"] * (len(poses) - 1))

    def smil(attr, a, b):
        if a == b:
            return ""
        vals = ";".join(f(b if p else a) for _, p in poses)
        return (f'<animate attributeName="{attr}" values="{vals}" keyTimes="{key_times}" dur="{T}s" '
                f'calcMode="spline" keySplines="{splines}" repeatCount="indefinite"/>')

    # draggable object (sits under the pinch point)
    obj = s.anim(move_frames(pinch_pt), iterations="infinite", total=T)
    s.defs.append(f'<linearGradient id="og" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{RISHI}"/><stop offset="1" stop-color="{BLUE}"/></linearGradient>')
    held = s.anim([(0, "opacity:0", None), (.6, "opacity:0", None), (.75, "opacity:1", None), (2.2, "opacity:1", None), (2.35, "opacity:0", None),
                   (3.0, "opacity:0", None), (3.15, "opacity:1", None), (4.6, "opacity:1", None), (4.75, "opacity:0", None), (T, "opacity:0", None)],
                  iterations="infinite", total=T)
    s.add(f'<g class="{obj}" style="transform:translate({f(A[0] + pinch_pt[0])}px,{f(A[1] + pinch_pt[1])}px)">'
          f'<rect x="-40" y="-27" width="80" height="54" rx="9" fill="url(#og)" opacity=".9"/>'
          f'<rect x="-30" y="-15" width="34" height="5" rx="2.5" fill="#fff" opacity=".9"/>'
          f'<rect x="-30" y="-5" width="50" height="4" rx="2" fill="#fff" opacity=".5"/>'
          f'<rect x="-30" y="3" width="42" height="4" rx="2" fill="#fff" opacity=".5"/>'
          f'<rect class="{held}" x="-43" y="-30" width="86" height="60" rx="11" stroke="#fff" stroke-width="1.5" style="opacity:0"/></g>')
    # hand
    hand = s.anim(move_frames(), iterations="infinite", total=T)
    s.add(f'<g class="{hand}" style="transform:translate({A[0]}px,{A[1]}px)">')
    for a, b in BONES:
        (x1, y1), (x2, y2) = HAND_OPEN[a], HAND_OPEN[b]
        (px1, py1), (px2, py2) = HAND_PINCH[a], HAND_PINCH[b]
        s.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#E4E4E7" stroke-opacity=".75" stroke-width="1.6" stroke-linecap="round">'
              f'{smil("x1", x1, px1)}{smil("y1", y1, py1)}{smil("x2", x2, px2)}{smil("y2", y2, py2)}</line>')
    for i, (x, y) in HAND_OPEN.items():
        px, py = HAND_PINCH[i]
        tip = i in (4, 8, 12, 16, 20)
        s.add(f'<circle cx="{x}" cy="{y}" r="{3 if tip else 2.4}" fill="#0C0C0F" stroke="{TEAL}" stroke-width="1.4">'
              f'{smil("cx", x, px)}{smil("cy", y, py)}</circle>')
    ring = s.anim([(0, "transform:scale(.3);opacity:0", None), (.6, "transform:scale(.3);opacity:0", OUT_EXPO), (.85, "transform:scale(1);opacity:1", None),
                   (2.15, "transform:scale(1);opacity:1", None), (2.35, "transform:scale(1.6);opacity:0", None),
                   (3.0, "transform:scale(.3);opacity:0", OUT_EXPO), (3.25, "transform:scale(1);opacity:1", None),
                   (4.55, "transform:scale(1);opacity:1", None), (4.75, "transform:scale(1.6);opacity:0", None), (T, "transform:scale(.3);opacity:0", None)],
                  iterations="infinite", total=T)
    s.add(f'<g transform="translate({f(pinch_pt[0])} {f(pinch_pt[1])})"><circle class="{ring}" r="10" stroke="{TEAL}" stroke-width="1.6" style="opacity:0"/></g>')
    s.add("</g>")
    # HUD
    blink = s.anim([(0, "opacity:1", None), (.8, "opacity:.35", None), (1.6, "opacity:1", None)], iterations="infinite")
    chip(s, 14, 12, "tracking · 21 landmarks", dot=lambda x, y: f'<circle class="{blink}" cx="{x}" cy="{y}" r="3" fill="{TEAL}"/>')
    mx = w - 14 - 60
    s.add(s.text(mx - 8, 25.5, "pinch", "mono", 10, TEXT_3, "end"))
    s.add(f'<rect x="{mx}" y="19" width="60" height="5" rx="2.5" fill="#1E1E24"/>')
    meter = s.anim([(t, f"transform:scaleX({.12 if p == 0 else .94})", EASE) for t, p in poses], iterations="infinite", total=T)
    s.add(f'<g transform="translate({mx} 19)"><rect class="{meter}" width="60" height="5" rx="2.5" fill="{TEAL}" style="transform:scaleX(.12)"/></g>')


# --- Autodesk: Intelligent Activity Log Analyzer ----------------------------
def draw_logs(s: Svg, w, h):
    T = 4.6
    rows = [92, 120, 76, 134, 104, 88, 128, 70]
    flagged = {2: AMBER, 5: AMBER}
    scan0, scan1 = .2, 2.4
    y0, step = 16, 10.5
    for i, mw in enumerate(rows):
        y = y0 + i * step
        s.add(f'<rect x="14" y="{y}" width="22" height="4" rx="2" fill="#26262C"/>')
        s.add(f'<rect x="42" y="{y}" width="{mw}" height="4" rx="2" fill="#202026"/>')
        if i in flagged:
            t = scan0 + (scan1 - scan0) * (y + 6) / (h + 14)
            fl = s.anim([(0, "opacity:0", None), (t, "opacity:0", None), (t + .15, "opacity:1", None), (3.9, "opacity:1", None),
                         (4.2, "opacity:0", None), (T, "opacity:0", None)], iterations="infinite", total=T)
            s.add(f'<g class="{fl}"><rect x="42" y="{y}" width="{mw}" height="4" rx="2" fill="{flagged[i]}"/>'
                  f'<circle cx="8" cy="{y + 2}" r="2" fill="{flagged[i]}"/></g>')
    s.defs.append(f'<linearGradient id="scan" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BLUE}" stop-opacity="0"/>'
                  f'<stop offset="1" stop-color="{BLUE}" stop-opacity=".22"/></linearGradient>')
    sc = s.anim([(0, "transform:translateY(-20px);opacity:0", None), (scan0, "transform:translateY(-20px);opacity:1", "ease-in-out"),
                 (scan1, f"transform:translateY({h}px);opacity:1", None), (scan1 + .01, f"transform:translateY({h}px);opacity:0", None),
                 (T, "transform:translateY(-20px);opacity:0", None)], iterations="infinite", total=T)
    s.add(f'<g class="{sc}" style="opacity:0"><rect width="{w}" height="18" fill="url(#scan)"/><line x1="0" y1="17.5" x2="{w}" y2="17.5" stroke="{BLUE}" stroke-opacity=".7"/></g>')
    cp = s.anim([(0, "opacity:0;transform:translateY(6px)", None), (scan1 + .05, "opacity:0;transform:translateY(6px)", OUT_EXPO),
                 (scan1 + .5, "opacity:1;transform:translateY(0)", None), (3.9, "opacity:1;transform:translateY(0)", None),
                 (4.2, "opacity:0;transform:translateY(0)", None), (T, "opacity:0", None)], iterations="infinite", total=T)
    label = "AI summary: 2 risky changes"
    cw = FONTS["sans-md"].width(label, 10) + 30
    s.add(f'<g class="{cp}"><rect x="{f(w / 2 - cw / 2)}" y="{h - 30}" width="{f(cw)}" height="20" rx="10" fill="#0E1A26" stroke="{BLUE}" stroke-opacity=".6"/>'
          + sparkle(w / 2 - cw / 2 + 12, h - 20, 5, BLUE) + s.text(w / 2 - cw / 2 + 21, h - 16.5, label, "sans-md", 10, "#CFE8FF") + "</g>")


# --- Autodesk: Team Settings redesign ----------------------------------------
def draw_settings(s: Svg, w, h):
    rows = ["Single sign-on", "Seat requests", "Audit log export"]
    T = 4.8
    for i, label in enumerate(rows):
        cy = 22 + i * 33
        if i:
            s.add(f'<line x1="14" y1="{cy - 16.5}" x2="{w - 14}" y2="{cy - 16.5}" stroke="{LINE}"/>')
        s.add(s.text(14, cy + 4, label, "sans", 11.5, TEXT_2))
        tx = w - 14 - 26
        on_at, off_at = .5 + i * .55, 2.9 + i * .45
        if i == 1:      # this one starts on and flips off/on
            on_at, off_at = off_at, on_at
        def fr(prop_on, prop_off):
            pts = sorted([(0, prop_off if i != 1 else prop_on), (on_at, prop_off), (on_at + .3, prop_on),
                          (off_at, prop_on), (off_at + .3, prop_off), (T, prop_off if i != 1 else prop_on)], key=lambda p: p[0])
            if i == 1:
                pts = [(0, prop_on), (off_at, prop_on), (off_at + .3, prop_off), (on_at, prop_off), (on_at + .3, prop_on), (T, prop_on)]
            return [(t, v, EASE) for t, v in pts]
        trk = s.anim(fr(f"fill:{BLUE}", "fill:#3A3A42"), iterations="infinite", total=T)
        knob = s.anim(fr("transform:translateX(12px)", "transform:translateX(0)"), iterations="infinite", total=T)
        on = i == 1   # static state: one toggle on
        s.add(f'<rect class="{trk}" x="{tx}" y="{cy - 7}" width="26" height="14" rx="7" fill="{BLUE if on else "#3A3A42"}"/>')
        s.add(f'<g transform="translate({tx + 7} {cy})"><circle class="{knob}" r="5" fill="#fff"'
              + (' style="transform:translateX(12px)"' if on else '') + '/></g>')


# --- MADLab: UofT Footprint --------------------------------------------------
def draw_footprint(s: Svg, w, h):
    T = 4.6
    for x, y, bw, bh in [(8, 8, 40, 22), (60, 8, 52, 22), (124, 8, 30, 22), (166, 8, 30, 22), (8, 44, 40, 26), (124, 44, 72, 26),
                         (8, 84, 40, 30), (60, 84, 52, 30), (124, 84, 30, 30), (166, 84, 30, 30)]:
        s.add(f'<rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="4" fill="#15161A"/>')
    s.add(f'<rect x="60" y="44" width="52" height="26" rx="13" fill="#0F1F17" stroke="#1C3A2A"/>')  # green quad
    path = [(24, 102), (54, 102), (54, 77), (118, 77), (118, 38), (160, 38)]
    dots, acc = [], 0.0
    segs = list(zip(path, path[1:]))
    total = sum(math.dist(a, b) for a, b in segs)
    n = 16
    for k in range(n):
        d = total * k / (n - 1)
        for a, b in segs:
            L = math.dist(a, b)
            if d <= L:
                dots.append((a[0] + (b[0] - a[0]) * d / L, a[1] + (b[1] - a[1]) * d / L))
                break
            d -= L
    t0, t1 = .2, 2.2
    for k, (x, y) in enumerate(dots[:-1]):
        t = t0 + (t1 - t0) * k / (n - 1)
        dt = s.anim([(0, "opacity:0", None), (t, "opacity:0", None), (t + .12, "opacity:1", None), (3.9, "opacity:1", None),
                     (4.3, "opacity:0", None), (T, "opacity:0", None)], iterations="infinite", total=T)
        s.add(f'<circle class="{dt}" cx="{f(x)}" cy="{f(y)}" r="1.8" fill="{TEXT_2}"/>')
    px, py = path[-1]
    rp = s.anim(pulse_frames([t1, t1 + 1], T, (1, 3), .6, 1.0), iterations="infinite", total=T)
    s.add(f'<g transform="translate({px} {py})"><circle class="{rp}" r="6" stroke="{TEAL}" stroke-width="1.5" style="opacity:0"/></g>')
    s.add(f'<circle cx="{px}" cy="{py}" r="5" fill="{TEAL}" stroke="#0C0C0F" stroke-width="2"/>')
    s.add(f'<circle cx="24" cy="102" r="4" fill="#0C0C0F" stroke="{TEXT_2}" stroke-width="1.5"/>')
    lab = s.anim([(0, "opacity:0;transform:translateY(4px)", None), (t1, "opacity:0;transform:translateY(4px)", OUT_EXPO),
                  (t1 + .4, "opacity:1;transform:translateY(0)", None), (3.9, "opacity:1;transform:translateY(0)", None),
                  (4.3, "opacity:0;transform:translateY(0)", None), (T, "opacity:0", None)], iterations="infinite", total=T)
    text = "quiet spot · 2 min"
    cw = FONTS["sans-md"].width(text, 10) + 16
    s.add(f'<g class="{lab}"><rect x="{f(px - cw + 14)}" y="{py - 30}" width="{f(cw)}" height="19" rx="9.5" fill="#102520" stroke="{TEAL}" stroke-opacity=".5"/>'
          + s.text(px - cw + 22, py - 17, text, "sans-md", 10, "#BDF5EA") + "</g>")


# --- Play: the experiments lab -----------------------------------------------
def draw_play(s: Svg, w, h):
    kx, ky, kr = 54, 56, 25
    for i in range(11):
        a = math.radians(-225 + i * 27)
        r1, r2 = kr + 6, kr + (11 if i in (0, 5, 10) else 9)
        s.add(f'<line x1="{f(kx + math.cos(a) * r1)}" y1="{f(ky + math.sin(a) * r1)}" x2="{f(kx + math.cos(a) * r2)}" '
              f'y2="{f(ky + math.sin(a) * r2)}" stroke="{TEXT_3}" stroke-width="1.4" stroke-linecap="round"/>')
    s.defs.append('<radialGradient id="kn" cx="40%" cy="35%" r="70%"><stop offset="0" stop-color="#2A2A31"/><stop offset="1" stop-color="#141417"/></radialGradient>')
    rot = s.anim([(0, "transform:rotate(-120deg)", "cubic-bezier(.6,0,.2,1)"), (1.6, "transform:rotate(95deg)", "cubic-bezier(.6,0,.2,1)"),
                  (2.4, "transform:rotate(40deg)", "cubic-bezier(.6,0,.2,1)"), (4, "transform:rotate(-120deg)", None)], iterations="infinite")
    s.add(f'<circle cx="{kx}" cy="{ky}" r="{kr}" fill="url(#kn)" stroke="{LINE_2}"/>')
    s.add(f'<g transform="translate({kx} {ky})"><g class="{rot}" style="transform:rotate(-120deg)">'
          f'<line x1="0" y1="-8" x2="0" y2="-{kr - 6}" stroke="{AMBER}" stroke-width="2.5" stroke-linecap="round"/></g></g>')
    # bouncing ball with squash & stretch
    bx, floor = 148, 92
    P = 1.1
    sh = s.anim([(0, "transform:scale(.45);opacity:.25", "cubic-bezier(.55,0,1,.45)"), (P / 2, "transform:scale(1);opacity:.6", "cubic-bezier(0,.55,.45,1)"),
                 (P, "transform:scale(.45);opacity:.25", None)], iterations="infinite")
    s.add(f'<g transform="translate({bx} {floor + 2})"><ellipse class="{sh}" rx="12" ry="2.5" fill="#000"/></g>')
    s.add(f'<line x1="{bx - 40}" y1="{floor + .5}" x2="{bx + 40}" y2="{floor + .5}" stroke="{LINE_2}"/>')
    ball = s.anim([(0, "transform:translateY(-62px) scale(.92,1.08)", "cubic-bezier(.55,0,1,.45)"),
                   (P / 2 - .04, "transform:translateY(-1px) scale(.9,1.1)", "linear"),
                   (P / 2, "transform:translateY(0) scale(1.28,.72)", "linear"),
                   (P / 2 + .05, "transform:translateY(-2px) scale(.9,1.1)", "cubic-bezier(0,.55,.45,1)"),
                   (P, "transform:translateY(-62px) scale(.96,1.04)", None)], iterations="infinite")
    s.add(f'<g transform="translate({bx} {floor})"><g class="{ball}" style="transform:translateY(-34px)"><circle cy="-10" r="10" fill="{RISHI}"/>'
          f'<circle cx="-3" cy="-13.5" r="3" fill="#fff" opacity=".35"/></g></g>')


# ============================================================ KIND WORDS ===
QUOTES = [
    ("quote-autodesk.svg", "Renata Lewis", "Principal Experience Designer, Autodesk", "RL",
     "I was so impressed by his talent and initiative. He quickly ramped up on a complex project and delivered "
     "high-quality design work. He also went above and beyond to find creative ways to experiment with AI and "
     "proposed thoughtful new solutions."),
    ("quote-wehear.svg", "Kanishka Patel", "CEO & Co-Founder, WeHear Innovations", "KP",
     "Rishi was an exceptional UX Designer intern at WeHear. His dedication and innovative user testing techniques "
     "significantly improved our product. His creativity and teamwork made a big impact on our projects."),
]


def quotes() -> None:
    w = 423
    size, lh = 12.5, 19.5
    wrapped = [wrap(q[4], "sans", size, w - 10 - 40 - 20) for q in QUOTES]
    n = max(len(x) for x in wrapped)
    h = 26 + n * lh + 64
    for (name, who, role, ini, text), lines in zip(QUOTES, wrapped):
        s = Svg(w, int(h), f"{who}, {role}: “{text}”", f"{who} on Rishi", "q")
        s.add(f'<rect x="5.5" y="5.5" width="{w - 11}" height="{int(h) - 11}" rx="12" fill="{PANEL}" stroke="{LINE}"/>')
        s.add(s.text(w - 22, 62, "”", "serif", 72, "#25252B", "end"))
        for i, line in enumerate(lines):
            first = "“" if i == 0 else ""
            last = "”" if i == len(lines) - 1 else ""
            s.add(s.text(22 if not first else 22 - FONTS["sans"].advance("“", size), 34 + i * lh, first + line + last, "sans", size, "#D4D4D8"))
        by = int(h) - 34
        s.add(f'<circle cx="35" cy="{by}" r="13" fill="#1C1C21" stroke="{LINE_2}"/>' + s.text(35, by + 3.8, ini, "sans-sb", 10, TEXT_2, "middle"))
        s.add(s.text(56, by - 2, who, "sans-sb", 12.5, TEXT) + s.text(56, by + 13, role, "sans", 11, TEXT_3))
        s.save(name)


# ============================================================ OFF CANVAS ===
def off_canvas() -> None:
    w, h = 846, 76
    s = Svg(w, h, "Off-canvas: photography, rap and music, geopolitics, storytelling.", "Off-canvas", "o")
    s.add(f'<rect x="5.5" y="5.5" width="{w - 11}" height="{h - 11}" rx="12" fill="{PANEL}" stroke="{LINE}"/>')
    cx, cy = 44, h / 2
    spin = s.anim([(0, "transform:rotate(0deg)", None), (3.2, "transform:rotate(360deg)", None)], iterations="infinite")
    grooves = "".join(f'<circle r="{r}" stroke="#26262C" stroke-width=".8"/>' for r in (11, 14, 17))
    s.add(f'<g transform="translate({cx} {f(cy)})"><g class="{spin}"><circle r="21" fill="#08080A" stroke="{LINE_2}"/>{grooves}'
          f'<path d="M-15 -8 A17 17 0 0 1 -6 -16" stroke="#fff" stroke-opacity=".22" stroke-width="2" stroke-linecap="round"/>'
          f'<circle r="7" fill="{RISHI}"/><circle r="1.6" fill="#08080A"/></g></g>')
    s.add(s.text(80, cy - 4, "Off-canvas", "sans-sb", 13, TEXT))
    s.add(s.text(80, cy + 14, "Photography · Rap & music · Geopolitics · Storytelling", "sans", 12, TEXT_2))
    import random
    rnd = random.Random(3)
    n = 14
    x0 = w - 26 - (n - 1) * 6
    for i in range(n):
        fr, t = [], 0
        while t < 1.4:
            fr.append((round(t, 3), f"transform:scaleY({f(.15 + rnd.random() * .85)})", "ease-in-out"))
            t += .12 + rnd.random() * .12
        fr.append((1.4, fr[0][1], None))
        bar = s.anim(fr, iterations="infinite", delay=-rnd.random())
        s.add(f'<g transform="translate({x0 + i * 6} {f(cy)})"><rect class="{bar}" x="-1.5" y="-13" width="3" height="26" rx="1.5" fill="{TEXT_3}"/></g>')
    s.add(s.text(x0 - 14, cy + 4, "on repeat", "mono", 10, TEXT_3, "end"))
    s.save("off-canvas.svg")


def main() -> None:
    hero()
    tile("tile-proberoom.svg", BIG, "ProbeRoom", "proberoom.vercel.app", "Rehearse user interviews out loud with an AI", "2026",
         "ProbeRoom: rehearse user interviews out loud with an AI participant.", draw_proberoom)
    tile("tile-gestura.svg", BIG, "Gestura", "MediaPipe · JS", "A canvas you drive with a pinch, no touch", "repo",
         "Gestura: a touchless canvas driven by hand pinch gestures tracked with MediaPipe.", draw_gestura)
    tile("tile-log-analyzer.svg", SMALL, "Log Analyzer", "Autodesk", "AI summaries for admin logs", None,
         "Autodesk Intelligent Activity Log Analyzer: AI summaries for dense admin activity logs.", draw_logs)
    tile("tile-team-settings.svg", SMALL, "Team Settings", "Autodesk", "Clearer settings for admins", None,
         "Autodesk Settings Redesign: clearer team settings for admins.", draw_settings)
    tile("tile-footprint.svg", SMALL, "UofT Footprint", "MADLab", "Low-pressure campus discovery", None,
         "UofT Footprint at MADLab: a low-pressure campus discovery feature.", draw_footprint)
    tile("tile-play.svg", SMALL, "Play", "lab", "Motion and sound experiments", None,
         "Play: experiments in motion and sound.", draw_play)
    quotes()
    off_canvas()


if __name__ == "__main__":
    main()
