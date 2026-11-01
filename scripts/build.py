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
    "inter": "https://registry.npmjs.org/@fontsource-variable/inter/-/inter-5.3.0.tgz",
    "manrope": "https://registry.npmjs.org/@fontsource-variable/manrope/-/manrope-5.3.0.tgz",
}
FONT_FILES = {
    # key: (package, path inside tarball, variable-font instance or None)
    "sans": ("geist", "package/dist/fonts/geist-sans/Geist-Regular.woff2", None),
    "sans-md": ("geist", "package/dist/fonts/geist-sans/Geist-Medium.woff2", None),
    "sans-sb": ("geist", "package/dist/fonts/geist-sans/Geist-SemiBold.woff2", None),
    "mono": ("geist", "package/dist/fonts/geist-mono/GeistMono-Regular.woff2", None),
    "serif": ("fraunces", "package/files/fraunces-latin-full-normal.woff2",
              {"opsz": 72, "wght": 400, "SOFT": 0, "WONK": 0}),
    # Figma's UI font (UI3 uses Inter at 450 / 550)
    "ui": ("inter", "package/files/inter-latin-wght-normal.woff2", {"wght": 450}),
    "ui-md": ("inter", "package/files/inter-latin-wght-normal.woff2", {"wght": 550}),
    "ui-sb": ("inter", "package/files/inter-latin-wght-normal.woff2", {"wght": 600}),
    # rishiashar.com body font
    "body": ("manrope", "package/files/manrope-latin-wght-normal.woff2", {"wght": 450}),
    "body-md": ("manrope", "package/files/manrope-latin-wght-normal.woff2", {"wght": 550}),
}
CSS_FAMILY = {
    "sans": ("GS", 400), "sans-md": ("GS", 500), "sans-sb": ("GS", 600),
    "mono": ("GM", 400), "serif": ("FR", 400),
    "ui": ("IN", 450), "ui-md": ("IN", 550), "ui-sb": ("IN", 600),
    "body": ("MR", 450), "body-md": ("MR", 550),
}
FALLBACK = {
    "GS": "ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif",
    "GM": "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace",
    "FR": "Georgia,'Times New Roman',serif",
    "IN": "Inter,ui-sans-serif,system-ui,-apple-system,'Segoe UI',sans-serif",
    "MR": "Manrope,ui-sans-serif,system-ui,sans-serif",
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
        used = {CSS_FAMILY[k][0] for k in self.chars}
        fams = "".join(f".{k.lower()}{{font-family:{k},{v}}}" for k, v in FALLBACK.items() if k in used)
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
# Figma UI3, dark theme.
FG_TABBAR = "#1F1F1F"
FG_PANEL = "#2C2C2C"
FG_CANVAS = "#1E1E1E"
FG_BORDER = "#444444"
FG_DIV = "#383838"
FG_FIELD = "#383838"
FG_T1 = "#FFFFFF"
FG_T2 = "#C2C2C2"
FG_T3 = "#8C8C8C"
FG_ROW_SEL = "#33475F"
SITE_BG = "#F5F5F5"     # rishiashar.com
SITE_INK = "#111111"


def vis_frames(windows, fade=.12):
    """Opacity keyframes for a list of visible windows [(start, end|None)]."""
    fr = [(0, f"opacity:{1 if windows[0][0] == 0 else 0}", None)]
    for a, b in windows:
        if a > 0:
            fr += [(a, "opacity:0", None), (a + fade, "opacity:1", None)]
        if b is not None:
            fr += [(b, "opacity:1", None), (b + fade, "opacity:0", None)]
    return fr


def chev(x, y, color=FG_T2, s=3.2):
    return (f'<path d="M{f(x - s)} {f(y - s / 2)} L{f(x)} {f(y + s / 2)} L{f(x + s)} {f(y - s / 2)}" '
            f'stroke="{color}" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>')


def plus(x, y, color=FG_T2):
    return f'<path d="M{x - 5} {y} H{x + 5} M{x} {y - 5} V{y + 5}" stroke="{color}" stroke-width="1.1" stroke-linecap="round"/>'


def eye(x, y, color=FG_T2):
    return (f'<path d="M{x - 6} {y} Q{x} {y - 6} {x + 6} {y} Q{x} {y + 6} {x - 6} {y}Z" stroke="{color}" stroke-width="1.1"/>'
            f'<circle cx="{x}" cy="{y}" r="1.8" fill="{color}"/>')


def figma_logo(x, y, s=1.0, color="#fff"):
    """Monochrome Figma mark, 12 x 18 units, top-left at (x, y)."""
    return (f'<g transform="translate({x} {y}) scale({s})" fill="{color}">'
            '<path d="M6 0H3A3 3 0 0 0 3 6H6Z"/><path d="M6 0H9A3 3 0 0 1 9 6H6Z"/>'
            '<path d="M6 6H3A3 3 0 0 0 3 12H6Z"/><circle cx="9" cy="9" r="3"/>'
            '<path d="M3 12H6V15A3 3 0 1 1 3 12Z"/></g>')


TOOL_ICONS = {
    "move": '<path d="M-4 -6.5 L-4 5.5 L-1 2.6 L1.3 7.3 L3.1 6.5 L.9 1.9 L4.9 1.9 Z" fill="#fff" stroke="#fff" stroke-width=".5" stroke-linejoin="round"/>',
    "frame": '<path d="M-2.5 -7 V7 M2.5 -7 V7 M-7 -2.5 H7 M-7 2.5 H7" stroke="#fff" stroke-width="1.2" stroke-linecap="round"/>',
    "rect": '<rect x="-6" y="-6" width="12" height="12" rx="1" stroke="#fff" stroke-width="1.2"/>',
    "pen": '<path d="M0 -7 L5 1 L2.5 6.5 H-2.5 L-5 1 Z M0 -7 V-.8" stroke="#fff" stroke-width="1.15" stroke-linejoin="round"/><circle cx="0" cy=".6" r="1.3" stroke="#fff" stroke-width="1.1"/>',
    "text": '<path d="M-5.5 -6 H5.5 M0 -6 V7" stroke="#fff" stroke-width="1.4" stroke-linecap="round"/>',
    "comment": '<path d="M-6.3 6.3 L-5.4 2.6 A6.5 6.5 0 1 1 -2.6 5.4 Z" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/>',
}
LAYER_ICONS = {
    "frame": '<path d="M-2.4 -5 V5 M2.4 -5 V5 M-5 -2.4 H5 M-5 2.4 H5" stroke="{c}" stroke-width="1" stroke-linecap="round"/>',
    "text": '<path d="M-4 -4.5 H4 M0 -4.5 V5" stroke="{c}" stroke-width="1.15" stroke-linecap="round"/>',
    "auto": '<rect x="-5" y="-5" width="10" height="10" rx="1.5" stroke="{c}"/><path d="M-2.5 -2 H2.5 M-2.5 0 H2.5 M-2.5 2 H2.5" stroke="{c}" stroke-linecap="round"/>',
}


def hero() -> None:
    W, H = 846, 480
    s = Svg(W, H, "A Figma file in dark mode. Rishi draws a frame and types 'Rishi Ashar' in Fraunces, "
            "Claude joins and fills in the about text and stack chips (TypeScript, React, Next.js, Framer Motion, "
            "WebGL, Web Audio), then Rishi opens the Prototype tab and drags a custom bezier into ease-out-expo. "
            "Experience designer in Toronto, designing and prototyping ideas with AI and emerging tools.",
            "Rishi Ashar", "h")
    TB, LP, RP = 36, 200, 610
    CX0, CX1 = LP + 1, RP - 1
    s.defs.append(f'<clipPath id="win"><rect width="{W}" height="{H}" rx="12"/></clipPath>')
    s.defs.append(f'<clipPath id="cv"><rect x="{CX0}" y="{TB}" width="{CX1 - CX0}" height="{H - TB}"/></clipPath>')
    s.defs.append('<filter id="sh" x="-20%" y="-40%" width="140%" height="200%">'
                  '<feDropShadow dx="0" dy="4" stdDeviation="8" flood-color="#000" flood-opacity=".45"/></filter>')

    # ---- timeline -------------------------------------------------------------
    T_FRAME_TOOL, T_DRAG0, T_DRAG1 = .35, .9, 1.7
    T_TEXT_TOOL, T_TEXT = 1.85, 2.25
    T_ESC = 3.45
    T_CL_IN, T_ABOUT, T_STACK, T_TOOLS = 3.6, 3.95, 4.35, 5.3
    T_SEL2, T_PROTO = 5.75, 6.1
    T_DRAG_B0, T_DRAG_B1 = 6.4, 7.05
    T_LOOP = 7.2
    T_IDLE = 7.6

    s.add('<g clip-path="url(#win)">')
    s.add(f'<rect width="{W}" height="{H}" fill="{FG_CANVAS}"/>')

    # ---- canvas: the frame (a slice of rishiashar.com) -------------------------
    fx, fw = 245, 320
    fy = 84
    nx, ny, nsize = fx + 22, fy + 60, 38
    about = wrap("Experience designer in Toronto, designing and prototyping ideas with AI and emerging tools.",
                 "body", 11, fw - 44)
    ay0 = ny + 25
    stack = ["TypeScript", "React", "Next.js", "Framer Motion", "WebGL", "Web Audio"]
    chips, cx, cy = [], nx, ay0 + len(about) * 16 + 10
    for name in stack:
        cw = FONTS["body-md"].width(name, 10) + 20
        if cx + cw > fx + fw - 22:
            cx, cy = nx, cy + 28
        chips.append((cx, cy, cw, name))
        cx += cw + 6
    chips_bottom = cy + 22
    tools_y = chips_bottom + 26
    tools_line = "Made with Figma, Cursor & Claude Code"
    fh = int(tools_y - fy + 18)

    # frame grows from top-left while dragging
    sx = s.anim([(0, "transform:scale(0,0);opacity:0", None), (T_DRAG0, "transform:scale(0,0);opacity:1", EASE),
                 (T_DRAG1, "transform:scale(1,1);opacity:1", None)])
    s.add(f'<g transform="translate({fx} {fy})"><g class="{sx}"><rect width="{fw}" height="{fh}" fill="{SITE_BG}"/></g></g>')

    # frame label: grey, overlaid by blue while selected
    lab_in = s.anim([(0, "opacity:0", None), (T_DRAG0 + .05, "opacity:0", None), (T_DRAG0 + .2, "opacity:1", None)])
    s.add(f'<g class="{lab_in}">' + s.text(fx, fy - 7, "Profile", "ui", 11, FG_T3) + "</g>")
    lab_sel = s.anim(vis_frames([(T_DRAG0 + .05, T_TEXT), (T_SEL2, None)], .05))
    s.add(f'<g class="{lab_sel}">' + s.text(fx, fy - 7, "Profile", "ui", 11, BLUE) + "</g>")

    # name, typed glyph by glyph
    name = "Rishi Ashar"
    x, char_times = nx, []
    for i, ch in enumerate(name):
        t = T_TEXT + .1 + i * .085
        char_times.append((t, x))
        if ch != " ":
            c = s.anim([(0, "opacity:0", "steps(1,end)"), (t, "opacity:1", None)])
            s.add(f'<g class="{c}">' + s.text(x, ny, ch, "serif", nsize, SITE_INK) + "</g>")
        x += FONTS["serif"].advance(ch, nsize)
    name_end = x
    t_typed = char_times[-1][0]
    # text box outline (auto-width grows while typing) + caret
    widths = [4] + [cx_ - nx + 4 for _, cx_ in char_times[1:]] + [name_end - nx + 4]
    kt = [0, T_TEXT / T_ESC] + [t / T_ESC for t, _ in char_times]
    vals = [widths[0], widths[0]] + widths[1:]
    tb = s.anim(vis_frames([(T_TEXT, T_ESC)], .02))
    s.add(f'<g class="{tb}" style="opacity:0"><rect x="{f(nx - 2)}" y="{ny - 33}" width="{f(name_end - nx + 4)}" height="44" stroke="{BLUE}">'
          f'<animate attributeName="width" values="{";".join(f(v) for v in vals)}" keyTimes="{";".join(f(min(k, 1)) for k in kt)}" '
          f'dur="{T_ESC}s" calcMode="discrete" fill="freeze"/></rect></g>')
    cf = [(0, f"opacity:0;transform:translateX({f(nx)}px)", "steps(1,end)"), (T_TEXT, f"opacity:1;transform:translateX({f(nx)}px)", "steps(1,end)")]
    for (t, _), cx_ in zip(char_times, [c for _, c in char_times[1:]] + [name_end]):
        cf.append((t + .001, f"opacity:1;transform:translateX({f(cx_)}px)", "steps(1,end)"))
    cf += [(t_typed + .3, f"opacity:0;transform:translateX({f(name_end)}px)", "steps(1,end)"),
           (t_typed + .6, f"opacity:1;transform:translateX({f(name_end)}px)", "steps(1,end)"),
           (T_ESC, f"opacity:0;transform:translateX({f(name_end)}px)", None)]
    s.add(f'<g class="{s.anim(cf)}" style="opacity:0"><rect x="0" y="{ny - 30}" width="1.5" height="38" fill="{BLUE}"/></g>')

    # Claude fills in about, stack chips, tools line
    ab = s.anim([(0, "opacity:0;transform:translateY(4px)", None), (T_ABOUT, "opacity:0;transform:translateY(4px)", OUT_EXPO),
                 (T_ABOUT + .45, "opacity:1;transform:translateY(0)", None)])
    s.add(f'<g class="{ab}">' + "".join(s.text(nx, ay0 + i * 16, ln, "body", 11, "#555555") for i, ln in enumerate(about)) + "</g>")
    chip_times = []
    for i, (cx_, cy_, cw, label) in enumerate(chips):
        t = T_STACK + .1 + i * .14
        chip_times.append(t)
        a = s.anim([(0, "opacity:0;transform:scale(.6)", None), (t, "opacity:0;transform:scale(.6)", OUT_EXPO),
                    (t + .4, "opacity:1;transform:scale(1)", None)])
        s.add(f'<g transform="translate({f(cx_ + cw / 2)} {cy_ + 11})"><g class="{a}">'
              f'<rect x="{f(-cw / 2)}" y="-11" width="{f(cw)}" height="22" rx="11" fill="#FFFFFF" stroke="#E2E2E2"/>'
              + s.text(0, 3.6, label, "body-md", 10, "#2A2A2A", "middle") + "</g></g>")
    tl = s.anim([(0, "opacity:0", None), (T_TOOLS, "opacity:0", None), (T_TOOLS + .3, "opacity:1", None)])
    s.add(f'<g class="{tl}">' + s.text(nx, tools_y, tools_line, "body", 10, "#8A8A8A") + "</g>")

    # Claude's selections (collaborator colour)
    about_w = max(FONTS["body"].width(ln, 11) for ln in about)
    boxes = [((nx - 3, ay0 - 12, about_w + 6, len(about) * 16 + 4), T_ABOUT, T_STACK),
             ((nx - 3, chips[0][1] - 3, fw - 44 + 6, chips_bottom - chips[0][1] + 6), T_STACK, T_TOOLS),
             ((nx - 3, tools_y - 12, FONTS["body"].width(tools_line, 10) + 6, 17), T_TOOLS, T_TOOLS + .45)]
    for (bx, by, bw, bh), a, b in boxes:
        c = s.anim(vis_frames([(a, b)], .05))
        s.add(f'<rect class="{c}" style="opacity:0" x="{f(bx)}" y="{f(by)}" width="{f(bw)}" height="{f(bh)}" stroke="{CLAUDE}" stroke-width="1.5"/>')

    # Rishi's selection on the frame: once while dragging, then again at the end
    hs = 7
    ktd = f(T_DRAG0 / T_DRAG1)
    spl = "0 0 1 1;.45 0 .2 1"

    def grow(attr, a, b):
        return (f'<animate attributeName="{attr}" values="{f(a)};{f(a)};{f(b)}" keyTimes="0;{ktd};1" dur="{T_DRAG1}s" '
                f'calcMode="spline" keySplines="{spl}" fill="freeze"/>')

    def handles(animated):
        out = ""
        for ox, oy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            an = ""
            if animated and ox:
                an += grow("x", fx - hs / 2, fx + fw - hs / 2)
            if animated and oy:
                an += grow("y", fy - hs / 2, fy + fh - hs / 2)
            out += (f'<rect x="{f(fx + ox * fw - hs / 2)}" y="{f(fy + oy * fh - hs / 2)}" width="{hs}" height="{hs}" '
                    f'fill="#fff" stroke="{BLUE}">{an}</rect>')
        return out

    sel1 = s.anim(vis_frames([(T_DRAG0, T_TEXT)], .02))
    s.add(f'<g class="{sel1}" style="opacity:0"><rect x="{fx}" y="{fy}" width="{fw}" height="{fh}" stroke="{BLUE}">'
          f'{grow("width", 0, fw)}{grow("height", 0, fh)}</rect>{handles(True)}</g>')
    sel2 = s.anim(vis_frames([(T_SEL2, None)], .02))
    s.add(f'<g class="{sel2}"><rect x="{fx}" y="{fy}" width="{fw}" height="{fh}" stroke="{BLUE}"/>{handles(False)}</g>')
    bt = f"{fw} × {fh}"
    bw_ = FONTS["ui-md"].width(bt, 11) + 10
    badge = s.anim(vis_frames([(T_DRAG1 - .02, T_TEXT), (T_SEL2, None)], .05))
    s.add(f'<g class="{badge}"><rect x="{f(fx + fw / 2 - bw_ / 2)}" y="{fy + fh + 8}" width="{f(bw_)}" height="17" rx="3" fill="{BLUE}"/>'
          + s.text(fx + fw / 2, fy + fh + 20, bt, "ui-md", 11, "#fff", "middle") + "</g>")

    # ---- floating toolbar (UI3) --------------------------------------------------
    tools = [("move", True), ("frame", True), ("rect", True), ("pen", True), ("text", False), ("comment", False)]
    cells, x = [], 8
    for tname, has_chev in tools:
        w = 42 if has_chev else 32
        cells.append((tname, x, w, has_chev))
        x += w + 2
    sep_x = x + 3
    x += 8
    act_x = x
    x += 34
    dev_x = x
    x += 48
    tb_w = x + 6
    tb_x = round((CX0 + CX1) / 2 - tb_w / 2)
    tb_y = H - 60
    s.add(f'<g transform="translate({tb_x} {tb_y})"><rect width="{tb_w}" height="48" rx="13" fill="{FG_PANEL}" stroke="{FG_BORDER}" stroke-width=".8" filter="url(#sh)"/>')
    active = {"move": [(0, T_FRAME_TOOL), (T_ESC, None)], "frame": [(T_FRAME_TOOL, T_TEXT_TOOL)], "text": [(T_TEXT_TOOL, T_ESC)]}
    for tname, cx_, w, has_chev in cells:
        if tname in active:
            hl = s.anim(vis_frames(active[tname], .01))
            base = "" if tname == "move" else ' style="opacity:0"'
            s.add(f'<rect class="{hl}"{base} x="{cx_}" y="8" width="{w}" height="32" rx="6" fill="{BLUE}"/>')
        s.add(f'<g transform="translate({cx_ + 16} 24)">{TOOL_ICONS[tname]}</g>')
        if has_chev:
            s.add(chev(cx_ + 34, 24.5, "#fff", 2.4))
    s.add(f'<line x1="{sep_x + .5}" y1="14" x2="{sep_x + .5}" y2="34" stroke="{FG_BORDER}"/>')
    s.add(f'<g transform="translate({act_x + 16} 24)"><path d="M0 -7 C.6 -2.4 2.4 -.6 7 0 C2.4 .6 .6 2.4 0 7 C-.6 2.4 -2.4 .6 -7 0 C-2.4 -.6 -.6 -2.4 0 -7Z" stroke="#fff" stroke-width="1.15" stroke-linejoin="round"/></g>')
    s.add(f'<g transform="translate({dev_x + 22} 24)"><rect x="-17" y="-10" width="34" height="20" rx="10" fill="{FG_FIELD}"/>'
          f'<circle cx="-7" cy="0" r="8" fill="#5A5A5A"/><path d="M-9.5 -2.2 L-11.6 0 L-9.5 2.2 M-4.5 -2.2 L-2.4 0 L-4.5 2.2 M-6.2 -3 L-7.8 3" '
          f'stroke="#fff" stroke-width="1" stroke-linecap="round" stroke-linejoin="round"/></g>')
    s.add("</g>")

    # ---- Claude's cursor (lives on the canvas) ------------------------------------
    def cursor_path(points, float_delay, amp):
        outer = s.anim([(t, f"transform:translate({f(x)}px,{f(y)}px)", e) for t, (x, y), e in points])
        fl = s.anim([(0, "transform:translate(0,0)", "ease-in-out"), (2.6, f"transform:translate({amp[0]}px,{amp[1]}px)", "ease-in-out"),
                     (5.2, "transform:translate(0,0)", None)], iterations="infinite", delay=float_delay)
        return outer, fl, points[-1][1]

    cl_pts = [(0, (CX1 + 30, 330), None), (T_CL_IN, (CX1 + 30, 330), EASE),
              (T_ABOUT, (nx + about_w + 4, ay0 + 16 * (len(about) - 1) + 2), None),
              (T_STACK, (nx + about_w + 4, ay0 + 16 * (len(about) - 1) + 2), EASE)]
    for (cx_, cy_, cw, _), t in zip(chips, chip_times):
        cl_pts.append((t, (cx_ + cw - 4, cy_ + 16), EASE))
    cl_pts += [(T_TOOLS, (nx + FONTS["body"].width(tools_line, 10), tools_y + 2), None),
               (T_TOOLS + .25, (nx + FONTS["body"].width(tools_line, 10), tools_y + 2), EASE),
               (T_SEL2 - .1, (fx - 14, fy + fh + 42), None)]
    o, fl, (lx, ly) = cursor_path(cl_pts, T_IDLE, (4, -3))
    s.add(f'<g clip-path="url(#cv)"><g class="{o}" style="transform:translate({f(lx)}px,{f(ly)}px)"><g class="{fl}">')
    s.add(f'<path d="M0 0 L0 15.5 L4.3 11.6 L7.2 18.2 L9.9 17 L7.1 10.6 L12.8 10.6 Z" fill="{CLAUDE}" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/>')
    chat = "added your stack"
    tag = s.anim([(0, "opacity:1", None), (T_TOOLS + .3, "opacity:1", None), (T_TOOLS + .45, "opacity:0", None),
                  (T_IDLE + 1.4, "opacity:0", None), (T_IDLE + 1.6, "opacity:1", None)])
    bub = s.anim([(0, "opacity:0;transform:scale(.6)", None), (T_TOOLS + .3, "opacity:0;transform:scale(.6)", OUT_EXPO),
                  (T_TOOLS + .65, "opacity:1;transform:scale(1)", None), (T_IDLE + 1.4, "opacity:1;transform:scale(1)", None),
                  (T_IDLE + 1.6, "opacity:0;transform:scale(.9)", None)])
    s.add(f'<g class="{tag}"><rect x="10" y="17" width="{f(FONTS["ui-md"].width("Claude", 11) + 12)}" height="18" rx="4" fill="{CLAUDE}"/>'
          + s.text(16, 29.5, "Claude", "ui-md", 11, "#fff") + "</g>")
    cw_ = FONTS["ui"].width(chat, 12) + 22
    s.add(f'<g transform="translate(10 17)"><g class="{bub}" style="opacity:0"><rect width="{f(cw_)}" height="26" rx="13" fill="{CLAUDE}"/>'
          + s.text(11, 17.5, chat, "ui", 12, "#fff") + "</g></g>")
    s.add("</g></g></g>")

    # ---- desktop tab bar -------------------------------------------------------------
    s.add(f'<rect width="{W}" height="{TB}" fill="{FG_TABBAR}"/>')
    for i, col in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        s.add(f'<circle cx="{18 + i * 18}" cy="18" r="5.5" fill="{col}"/>')
    s.add(f'<path d="M80 19.5 L86 14 L92 19.5 V24 H80Z" stroke="{FG_T2}" stroke-width="1.1" stroke-linejoin="round"/>')
    tab_x, tab_w = 104, 188
    s.add(f'<path d="M{tab_x} {TB} V{12} Q{tab_x} 6 {tab_x + 6} 6 H{tab_x + tab_w - 6} Q{tab_x + tab_w} 6 {tab_x + tab_w} 12 V{TB}Z" fill="{FG_PANEL}"/>')
    s.add(figma_logo(tab_x + 12, 15, .5, FG_T2) + s.text(tab_x + 26, 24, "rishiashar.com", "ui-md", 11, FG_T1))
    s.add(f'<path d="M{tab_x + tab_w - 18} 17.5 l5 5 m0 -5 l-5 5" stroke="{FG_T2}" stroke-width="1.1" stroke-linecap="round"/>')
    s.add(plus(tab_x + tab_w + 18, 21, FG_T2))

    # ---- left panel ---------------------------------------------------------------------
    s.add(f'<rect x="0" y="{TB}" width="{LP}" height="{H - TB}" fill="{FG_PANEL}"/>'
          f'<line x1="{LP + .5}" y1="{TB}" x2="{LP + .5}" y2="{H}" stroke="{FG_BORDER}" stroke-width=".8"/>')
    s.add(figma_logo(16, 50, .78) + chev(36, 57, FG_T2, 2.6))
    s.add(f'<rect x="{LP - 30}" y="49" width="14" height="14" rx="2.5" stroke="{FG_T2}" stroke-width="1.1"/>'
          f'<line x1="{LP - 25.5}" y1="49" x2="{LP - 25.5}" y2="63" stroke="{FG_T2}" stroke-width="1.1"/>')
    fname = "rishiashar.com"
    s.add(s.text(16, 92, fname, "ui-sb", 12, FG_T1) + chev(16 + FONTS["ui-sb"].width(fname, 12) + 9, 88, FG_T2, 2.8))
    s.add(s.text(16, 108, "Drafts", "ui", 11, FG_T3))
    s.add(f'<line x1="0" y1="122.5" x2="{LP}" y2="122.5" stroke="{FG_DIV}"/>')
    s.add(f'<rect x="10" y="129" width="{FONTS["ui-sb"].width("File", 11) + 16}" height="24" rx="5" fill="{FG_FIELD}"/>')
    s.add(s.text(18, 145, "File", "ui-sb", 11, FG_T1) + s.text(18 + FONTS["ui-sb"].width("File", 11) + 22, 145, "Assets", "ui-md", 11, FG_T3))
    s.add(f'<circle cx="{LP - 24}" cy="140" r="4.5" stroke="{FG_T2}" stroke-width="1.1"/><line x1="{LP - 20.5}" y1="143.5" x2="{LP - 17}" y2="147" stroke="{FG_T2}" stroke-width="1.1" stroke-linecap="round"/>')
    s.add(s.text(16, 178, "Pages", "ui-sb", 11, FG_T1) + plus(LP - 22, 174))
    for i, pg in enumerate(["Profile", "Work", "Play"]):
        y = 188 + i * 26
        if i == 0:
            s.add(f'<path d="M15 {y + 13} l2.6 2.6 l4.6 -5.2" stroke="{FG_T1}" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>')
        s.add(s.text(30, y + 17, pg, "ui-md" if i == 0 else "ui", 11, FG_T1 if i == 0 else FG_T2))
    s.add(f'<line x1="0" y1="274.5" x2="{LP}" y2="274.5" stroke="{FG_DIV}"/>')
    s.add(s.text(16, 298, "Layers", "ui-sb", 11, FG_T1))

    # layers: newest child on top, like Figma
    ROW, ly0 = 28, 308
    kids = [("text", "Rishi Ashar", T_TEXT), ("text", "About", T_ABOUT), ("auto", "Stack", T_STACK), ("text", "Tools", T_TOOLS)]
    sel_row = s.anim(vis_frames([(T_DRAG0 + .05, T_TEXT), (T_SEL2, None)], .01))
    s.add(f'<rect class="{sel_row}" x="8" y="{ly0}" width="{LP - 16}" height="{ROW - 2}" rx="5" fill="{FG_ROW_SEL}"/>')
    ap = s.anim([(0, "opacity:0", None), (T_DRAG0 + .05, "opacity:0", None), (T_DRAG0 + .25, "opacity:1", None)])
    s.add(f'<g class="{ap}">{chev(16, ly0 + 13, FG_T2, 2.4)}<g transform="translate(29 {ly0 + 13})">{LAYER_ICONS["frame"].format(c=FG_T1)}</g>'
          + s.text(42, ly0 + 17, "Profile", "ui-md", 11, FG_T1) + "</g>")
    sel_kid = s.anim(vis_frames([(T_TEXT, T_ESC)], .01))
    s.add(f'<rect class="{sel_kid}" style="opacity:0" x="8" y="{ly0 + ROW}" width="{LP - 16}" height="{ROW - 2}" rx="5" fill="{FG_ROW_SEL}"/>')
    for i, (kind, label, t0) in enumerate(kids):
        later = [t for _, _, t in kids[i + 1:]]
        fr = [(0, f"opacity:0;transform:translate(-6px,{ly0 + ROW}px)", None), (t0, f"opacity:0;transform:translate(-6px,{ly0 + ROW}px)", OUT_EXPO),
              (t0 + .35, f"opacity:1;transform:translate(0,{ly0 + ROW}px)", None)]
        for k, t in enumerate(later, 2):
            fr += [(t, f"opacity:1;transform:translate(0,{ly0 + ROW * (k - 1)}px)", OUT_EXPO),
                   (t + .3, f"opacity:1;transform:translate(0,{ly0 + ROW * k}px)", None)]
        final_y = ly0 + ROW * (len(kids) - i)
        a = s.anim(fr)
        s.add(f'<g class="{a}" style="transform:translate(0,{final_y}px)"><g transform="translate(43 13)">{LAYER_ICONS[kind].format(c=FG_T2)}</g>'
              + s.text(56, 17, label, "ui", 11, FG_T2) + "</g>")

    # ---- right panel ------------------------------------------------------------------------
    s.add(f'<rect x="{RP}" y="{TB}" width="{W - RP}" height="{H - TB}" fill="{FG_PANEL}"/>'
          f'<line x1="{RP - .5}" y1="{TB}" x2="{RP - .5}" y2="{H}" stroke="{FG_BORDER}" stroke-width=".8"/>')
    px0, px1 = RP + 12, W - 12
    # header: avatars, present, share
    av = s.anim([(0, "opacity:0;transform:scale(.4)", None), (T_CL_IN, "opacity:0;transform:scale(.4)", OUT_EXPO),
                 (T_CL_IN + .5, "opacity:1;transform:scale(1)", None)])
    s.add(f'<g transform="translate({px0 + 30} 60)"><g class="{av}"><circle r="12" fill="{CLAUDE}" stroke="{FG_PANEL}" stroke-width="2"/>{claude_mark(0, 0, 7)}</g></g>')
    s.add(f'<g transform="translate({px0 + 12} 60)"><circle r="12" fill="{RISHI}" stroke="{FG_PANEL}" stroke-width="2"/>'
          + s.text(0, 4, "R", "ui-sb", 11, "#fff", "middle") + "</g>")
    s.add(f'<path d="M{px1 - 92} 54 L{px1 - 83} 60 L{px1 - 92} 66Z" stroke="{FG_T1}" stroke-width="1.15" stroke-linejoin="round"/>' + chev(px1 - 74, 60.5, FG_T2, 2.4))
    s.add(f'<rect x="{px1 - 58}" y="47" width="58" height="26" rx="6" fill="{BLUE}"/>' + s.text(px1 - 29, 64, "Share", "ui-md", 11, "#fff", "middle"))
    # tabs + zoom
    tabs_y = 104
    wd = FONTS["ui-sb"].width("Design", 11)
    wp = FONTS["ui-sb"].width("Prototype", 11)
    t_design = s.anim(vis_frames([(0, T_PROTO)], .08))
    t_proto = s.anim(vis_frames([(T_PROTO, None)], .08))
    s.add(f'<rect class="{t_design}" style="opacity:0" x="{px0 - 2}" y="{tabs_y - 16}" width="{f(wd + 16)}" height="24" rx="5" fill="{FG_FIELD}"/>')
    s.add(f'<rect class="{t_proto}" x="{f(px0 + wd + 18)}" y="{tabs_y - 16}" width="{f(wp + 16)}" height="24" rx="5" fill="{FG_FIELD}"/>')
    s.add(s.text(px0 + 6, tabs_y, "Design", "ui-sb", 11, FG_T3) + f'<g class="{t_design}" style="opacity:0">' + s.text(px0 + 6, tabs_y, "Design", "ui-sb", 11, FG_T1) + "</g>")
    s.add(s.text(px0 + wd + 26, tabs_y, "Prototype", "ui-sb", 11, FG_T3) + f'<g class="{t_proto}">' + s.text(px0 + wd + 26, tabs_y, "Prototype", "ui-sb", 11, FG_T1) + "</g>")
    s.add(s.text(px1 - 12, tabs_y, "100%", "ui", 11, FG_T2, "end") + chev(px1 - 3, tabs_y - 3.5, FG_T2, 2.4))
    s.add(f'<line x1="{RP}" y1="120.5" x2="{W}" y2="120.5" stroke="{FG_DIV}"/>')

    def field(x, y, w, h=26):
        return f'<rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{h}" rx="5" fill="{FG_FIELD}"/>'

    def kv(x, y, w, k, v, kfont="ui"):
        return field(x, y, w) + s.text(x + 9, y + 17, k, kfont, 11, FG_T3) + s.text(x + 24, y + 17, v, "ui", 11, FG_T1)

    def head(y, label, icon=None):
        return s.text(px0 + 4, y, label, "ui-sb", 11, FG_T1) + (icon or "")

    def div(y):
        return f'<line x1="{RP}" y1="{y + .5}" x2="{W}" y2="{y + .5}" stroke="{FG_DIV}"/>'

    def swatch_row(y, hexv, color):
        return (field(px0, y, 158) + f'<rect x="{px0 + 7}" y="{y + 6}" width="14" height="14" rx="3" fill="{color}" stroke="#555"/>'
                + s.text(px0 + 29, y + 17, hexv, "ui", 11, FG_T1) + s.text(px0 + 150, y + 17, "100%", "ui", 11, FG_T1, "end")
                + eye(px1 - 34, y + 13) + f'<path d="M{px1 - 13} {y + 13} H{px1 - 5}" stroke="{FG_T2}" stroke-width="1.1" stroke-linecap="round"/>')

    hw = (px1 - px0 - 8) / 2
    groups = {}
    # nothing selected
    g = [head(146, "Page"), swatch_row(156, "1E1E1E", FG_CANVAS), div(194),
         head(218, "Variables", f'<path d="M{px1 - 17} 211 H{px1 - 3} M{px1 - 17} 219 H{px1 - 3}" stroke="{FG_T2}" stroke-width="1.1" stroke-linecap="round"/>'
              f'<circle cx="{px1 - 13}" cy="211" r="2.2" fill="{FG_PANEL}" stroke="{FG_T2}" stroke-width="1.1"/>'
              f'<circle cx="{px1 - 7}" cy="219" r="2.2" fill="{FG_PANEL}" stroke="{FG_T2}" stroke-width="1.1"/>'), div(234),
         head(258, "Styles", plus(px1 - 10, 254)), div(274), head(298, "Export", plus(px1 - 10, 294))]
    groups["page"] = ("".join(g), [(0, T_DRAG0), (T_ESC, T_SEL2)])
    # frame selected
    align = "".join(f'<path d="{d}" stroke="{FG_T2}" stroke-width="1.1" stroke-linecap="round"/>' for d in (
        f"M{px0 + 12} 189 V201 M{px0 + 15} 192 H{px0 + 22} M{px0 + 15} 198 H{px0 + 20}",
        f"M{px0 + 40} 189 V201 M{px0 + 36} 192 H{px0 + 44} M{px0 + 37} 198 H{px0 + 43}",
        f"M{px0 + 66} 189 V201 M{px0 + 56} 192 H{px0 + 63} M{px0 + 58} 198 H{px0 + 63}",
        f"M{px0 + 82} 188 H{px0 + 94} M{px0 + 85} 191 V198 M{px0 + 91} 191 V196",
        f"M{px0 + 104} 194 H{px0 + 116} M{px0 + 107} 190 V198 M{px0 + 113} 191 V197",
        f"M{px0 + 126} 200 H{px0 + 138} M{px0 + 129} 190 V197 M{px0 + 135} 192 V197"))
    g = [head(146, "Frame") + chev(px0 + 4 + FONTS["ui-sb"].width("Frame", 11) + 9, 142.5, FG_T2, 2.6),
         head(176, "Position"), field(px0, 182, px1 - px0), align,
         kv(px0, 214, hw, "X", str(fx)), kv(px0 + hw + 8, 214, hw, "Y", str(fy)), div(252),
         head(276, "Layout"), kv(px0, 286, hw, "W", str(fw)), kv(px0 + hw + 8, 286, hw, "H", str(fh)), div(324),
         head(348, "Appearance"),
         field(px0, 358, hw), f'<rect x="{px0 + 8}" y="365" width="11" height="11" rx="2" stroke="{FG_T3}" stroke-width="1.1" stroke-dasharray="2 1.5"/>',
         s.text(px0 + 26, 375, "100%", "ui", 11, FG_T1),
         field(px0 + hw + 8, 358, hw),
         f'<path d="M{f(px0 + hw + 17)} 377 V370 Q{f(px0 + hw + 17)} 365 {f(px0 + hw + 22)} 365 H{f(px0 + hw + 28)}" stroke="{FG_T3}" stroke-width="1.1" stroke-linecap="round"/>',
         s.text(px0 + hw + 34, 375, "0", "ui", 11, FG_T1),
         div(396), head(420, "Fill", plus(px1 - 10, 416)), swatch_row(430, "F5F5F5", SITE_BG)]
    groups["frame"] = ("".join(g), [(T_DRAG0, T_TEXT), (T_SEL2, T_PROTO)])
    # text selected
    g = [head(146, "Text"), head(176, "Position"), kv(px0, 186, hw, "X", "22"), kv(px0 + hw + 8, 186, hw, "Y", "26"), div(224),
         head(248, "Typography"), field(px0, 258, px1 - px0), s.text(px0 + 9, 275, "Fraunces", "ui", 11, FG_T1), chev(px1 - 12, 271, FG_T2, 2.6),
         field(px0, 290, hw + 30), s.text(px0 + 9, 307, "Regular", "ui", 11, FG_T1), chev(px0 + hw + 20, 303, FG_T2, 2.6),
         field(px0 + hw + 38, 290, hw - 30), s.text(px0 + hw + 47, 307, str(nsize), "ui", 11, FG_T1), chev(px1 - 12, 303, FG_T2, 2.6),
         field(px0, 322, hw), f'<path d="M{px0 + 8} 329 H{px0 + 18} M{px0 + 8} 341 H{px0 + 18} M{px0 + 13} 332 V338" stroke="{FG_T3}" stroke-width="1.1" stroke-linecap="round"/>',
         s.text(px0 + 24, 339, "Auto", "ui", 11, FG_T1),
         field(px0 + hw + 8, 322, hw), s.text(px0 + hw + 17, 339, "|A|", "ui", 10, FG_T3), s.text(px0 + hw + 36, 339, "-2%", "ui", 11, FG_T1),
         div(362), head(386, "Fill", plus(px1 - 10, 382)), swatch_row(396, "111111", SITE_INK)]
    groups["text"] = ("".join(g), [(T_TEXT, T_ESC)])
    # prototype tab: interaction + custom bezier editor
    ex0, ey0, ew, eh = px0, 298, px1 - px0, 104
    q0 = (ex0 + 18, ey0 + eh - 16)
    q3 = (ex0 + ew - 18, ey0 + 16)
    ddx, ddy = q3[0] - q0[0], q3[1] - q0[1]

    def bz(x1, y1):
        return (q0[0] + x1 * ddx, q0[1] + y1 * ddy)
    p1a, p1b, p2 = bz(.42, 0), bz(.16, 1), bz(.3, 1)
    da = f"M{f(q0[0])} {f(q0[1])} C{f(p1a[0])} {f(p1a[1])} {f(p2[0])} {f(p2[1])} {f(q3[0])} {f(q3[1])}"
    db = f"M{f(q0[0])} {f(q0[1])} C{f(p1b[0])} {f(p1b[1])} {f(p2[0])} {f(p2[1])} {f(q3[0])} {f(q3[1])}"
    k0, k1 = f(T_DRAG_B0 / T_DRAG_B1), "1"

    def mv(attr, a, b):
        return (f'<animate attributeName="{attr}" values="{a};{a};{b}" keyTimes="0;{k0};{k1}" dur="{T_DRAG_B1}s" '
                f'calcMode="spline" keySplines="0 0 1 1;.45 0 .2 1" fill="freeze"/>')
    grid = "".join(f'<line x1="{f(ex0 + ew * k / 4)}" y1="{ey0}" x2="{f(ex0 + ew * k / 4)}" y2="{ey0 + eh}" stroke="#262626"/>' for k in (1, 2, 3))
    grid += "".join(f'<line x1="{ex0}" y1="{f(ey0 + eh * k / 3)}" x2="{ex0 + ew}" y2="{f(ey0 + eh * k / 3)}" stroke="#262626"/>' for k in (1, 2))
    vals_a, vals_b = "0.42, 0, 0.3, 1", "0.16, 1, 0.3, 1"
    va = s.anim(vis_frames([(0, T_DRAG_B1)], .05))
    vb = s.anim(vis_frames([(T_DRAG_B1, None)], .05))
    g = [head(146, "Interactions", plus(px1 - 10, 142)),
         field(px0, 156, px1 - px0, 30),
         f'<circle cx="{px0 + 14}" cy="171" r="5" stroke="{FG_T2}" stroke-width="1.1"/><circle cx="{px0 + 14}" cy="171" r="1.6" fill="{FG_T2}"/>',
         s.text(px0 + 26, 175, "On click", "ui", 11, FG_T2),
         (lambda ax: f'<path d="M{f(ax)} 171 H{f(ax + 10)} M{f(ax + 6.5)} 167.5 L{f(ax + 10)} 171 L{f(ax + 6.5)} 174.5" stroke="{FG_T3}" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>'
          + s.text(ax + 17, 175, "Work", "ui-md", 11, FG_T1))(px0 + 26 + FONTS["ui"].width("On click", 11) + 8),
         div(198), head(222, "Animation"),
         field(px0, 232, px1 - px0), s.text(px0 + 9, 249, "Smart animate", "ui", 11, FG_T1), chev(px1 - 12, 245, FG_T2, 2.6),
         field(px0, 264, px1 - px0),
         f'<path d="M{px0 + 8} 283 C{px0 + 11} 273 {px0 + 14} 271 {px0 + 20} 271" stroke="{FG_T2}" stroke-width="1.1" stroke-linecap="round"/>',
         s.text(px0 + 26, 281, "Custom bezier", "ui", 11, FG_T1), chev(px1 - 12, 277, FG_T2, 2.6)]
    g += [f'<rect x="{ex0}" y="{ey0}" width="{ew}" height="{eh}" rx="6" fill="{FG_CANVAS}" stroke="{FG_DIV}"/>', grid,
          f'<path d="M{f(q0[0])} {f(q0[1])} L{f(q3[0])} {f(q3[1])}" stroke="#3A3A3A" stroke-dasharray="2 3"/>',
          f'<line x1="{f(q3[0])}" y1="{f(q3[1])}" x2="{f(p2[0])}" y2="{f(p2[1])}" stroke="{BLUE}"/>',
          f'<line x1="{f(q0[0])}" y1="{f(q0[1])}" x2="{f(p1b[0])}" y2="{f(p1b[1])}" stroke="{BLUE}">{mv("x2", f(p1a[0]), f(p1b[0]))}{mv("y2", f(p1a[1]), f(p1b[1]))}</line>',
          f'<path d="{db}" stroke="#fff" stroke-width="2" stroke-linecap="round">'
          f'<animate attributeName="d" values="{da};{da};{db}" keyTimes="0;{k0};1" dur="{T_DRAG_B1}s" calcMode="spline" keySplines="0 0 1 1;.45 0 .2 1" fill="freeze"/></path>',
          f'<circle cx="{f(p2[0])}" cy="{f(p2[1])}" r="4" fill="{FG_CANVAS}" stroke="{BLUE}" stroke-width="1.5"/>',
          f'<circle cx="{f(p1b[0])}" cy="{f(p1b[1])}" r="4" fill="#fff" stroke="{BLUE}" stroke-width="1.5">{mv("cx", f(p1a[0]), f(p1b[0]))}{mv("cy", f(p1a[1]), f(p1b[1]))}</circle>',
          f'<circle cx="{f(q0[0])}" cy="{f(q0[1])}" r="2.5" fill="#fff"/><circle cx="{f(q3[0])}" cy="{f(q3[1])}" r="2.5" fill="#fff"/>']
    fy2 = ey0 + eh + 8
    g += [field(px0, fy2, 150), f'<g class="{va}" style="opacity:0">' + s.text(px0 + 9, fy2 + 17, vals_a, "ui", 11, FG_T1) + "</g>",
          f'<g class="{vb}">' + s.text(px0 + 9, fy2 + 17, vals_b, "ui", 11, FG_T1) + "</g>",
          field(px0 + 158, fy2, px1 - px0 - 158), s.text(px0 + 167, fy2 + 17, "600ms", "ui", 11, FG_T1)]
    # live preview of the easing
    tr_y = fy2 + 46
    tr0, tr1 = px0 + 10, px1 - 10
    g.append(f'<line x1="{tr0}" y1="{tr_y}" x2="{tr1}" y2="{tr_y}" stroke="{FG_FIELD}" stroke-width="2" stroke-linecap="round"/>')
    groups["proto"] = ("".join(g), [(T_PROTO, None)])

    for key, (body, wins) in groups.items():
        c = s.anim(vis_frames(wins, .1))
        base = "" if key == "proto" else ' style="opacity:0"'
        s.add(f'<g class="{c}"{base}>{body}</g>')
    # the preview dot (separate so it can loop)
    P, L = 2.4, tr1 - tr0
    loop = [(0, "transform:translateX(0)", OUT_EXPO), (P * .25, f"transform:translateX({L}px)", None),
            (P * .5, f"transform:translateX({L}px)", OUT_EXPO), (P * .75, "transform:translateX(0)", None), (P, "transform:translateX(0)", None)]
    dot = s.anim(loop, iterations="infinite", delay=T_LOOP)
    show = s.anim(vis_frames([(T_PROTO, None)], .1))
    s.add(f'<g class="{show}"><g transform="translate({tr0} {tr_y})"><g class="{dot}"><circle r="9" fill="{BLUE}" opacity=".2"/><circle r="5" fill="#fff"/></g></g></g>')

    # ---- Rishi's cursor (on top of everything) ---------------------------------------------
    tab_px = px0 + wd + 26 + wp / 2
    ri_pts = [
        (0, (fx + fw - 30, fy + 250), None), (T_FRAME_TOOL, (fx + fw - 30, fy + 250), EASE), (T_DRAG0, (fx, fy), EASE),
        (T_DRAG1, (fx + fw, fy + fh), None), (T_TEXT_TOOL, (fx + fw, fy + fh), EASE), (T_TEXT - .05, (nx + 2, ny - 22), None),
        (T_TEXT + .1, (nx + 2, ny - 22), EASE), (T_TEXT + .6, (fx + fw - 34, fy + 30), None),
        (T_SEL2 - .4, (fx + fw - 34, fy + 30), EASE), (T_SEL2, (fx + 22, fy - 10), None), (T_SEL2 + .05, (fx + 22, fy - 10), EASE),
        (T_PROTO - .02, (tab_px, tabs_y - 6), None), (T_PROTO + .1, (tab_px, tabs_y - 6), EASE),
        (T_DRAG_B0, p1a, EASE), (T_DRAG_B1, p1b, None), (T_DRAG_B1 + .1, p1b, EASE),
        (T_IDLE, (p1b[0] + 34, p1b[1] + 44), None),
    ]
    o, fl, (lx, ly) = cursor_path(ri_pts, T_IDLE, (-4, 3))
    s.add(f'<g class="{o}" style="transform:translate({f(lx)}px,{f(ly)}px)"><g class="{fl}">'
          f'<path d="M0 0 L0 15.5 L4.3 11.6 L7.2 18.2 L9.9 17 L7.1 10.6 L12.8 10.6 Z" fill="{RISHI}" stroke="#fff" stroke-width="1.2" stroke-linejoin="round"/>'
          f'<rect x="10" y="17" width="{f(FONTS["ui-md"].width("Rishi", 11) + 12)}" height="18" rx="4" fill="{RISHI}"/>'
          + s.text(16, 29.5, "Rishi", "ui-md", 11, "#fff") + "</g></g>")

    s.add("</g>")  # /clip window
    s.add(f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" stroke="{FG_BORDER}"/>')
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
