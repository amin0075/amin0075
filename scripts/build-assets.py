#!/usr/bin/env python3
"""Builds the branded SVGs behind this profile README.

GitHub can't load web fonts inside README images, so every word is drawn as
vector outlines in the brand fonts (Space Grotesk, Inter, Geist Mono), with
the fonts' own kerning. Each image comes in a dark and a light version; the
README picks one with <picture> and the reader's GitHub theme.

    python scripts/build-assets.py --fonts DIR [--fonts DIR ...]

The --fonts folders must hold SpaceGrotesk-Variable.ttf, Inter-Variable.ttf
and GeistMono-Variable.woff2. Needs fontTools; Geist Mono is woff2, so
without the `brotli` module the script borrows Node's zlib to read it.
"""

import argparse
import base64
import html
import subprocess
import sys
import types
from pathlib import Path

try:
    import brotli  # noqa: F401
except ImportError:  # decompress-only stand-in, enough to read woff2

    def _decompress(data):
        js = "process.stdout.write(require('zlib').brotliDecompressSync(require('fs').readFileSync(0)))"
        return subprocess.run(
            ["node", "-e", js], input=data, capture_output=True, check=True
        ).stdout

    sys.modules["brotli"] = types.SimpleNamespace(decompress=_decompress)

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets" / "profile"
SOURCES = ASSETS / "src"

# Website tokens (amin-keshavarzi.com globals.css), as in the brand file.
PALETTES = {
    "dark": dict(
        ground="#110F0D",
        surface="#1C1917",
        line="#2F2C29",
        text="#F2F0EA",
        muted="#A5A39B",
        accent="#E85A4F",
        on_accent="#110F0D",
    ),
    "light": dict(
        ground="#F6F4ED",
        surface="#FCFBF7",
        line="#DBD7CB",
        text="#15120F",
        muted="#62605A",
        accent="#BC3830",
        on_accent="#F6F4ED",
    ),
}

HANDLE = "@amin_decods"
SITE = "amin-keshavarzi.com"


# ---------------------------------------------------------------------------
# Type: variable fonts pinned to one weight, laid out with GPOS pair kerning.
# ---------------------------------------------------------------------------


def _num(value):
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


class Face:
    def __init__(self, path, **axes):
        font = TTFont(path)
        location = {axis.axisTag: axis.defaultValue for axis in font["fvar"].axes}
        location.update(axes)
        self.name = f"{Path(path).stem} {axes}"
        self.font = instancer.instantiateVariableFont(font, location)
        self.upm = self.font["head"].unitsPerEm
        self.cmap = self.font.getBestCmap()
        self.glyphs = self.font.getGlyphSet()
        self.advance = {name: m[0] for name, m in self.font["hmtx"].metrics.items()}
        self.pairs = self._pair_subtables()
        self.kern_cache = {}

    def _pair_subtables(self):
        if "GPOS" not in self.font:
            return []
        gpos = self.font["GPOS"].table
        indices = sorted(
            {
                index
                for record in gpos.FeatureList.FeatureRecord
                if record.FeatureTag == "kern"
                for index in record.Feature.LookupListIndex
            }
        )
        subtables = []
        for index in indices:
            lookup = gpos.LookupList.Lookup[index]
            for sub in lookup.SubTable:
                if lookup.LookupType == 9:
                    if sub.ExtensionLookupType != 2:
                        continue
                    sub = sub.ExtSubTable
                elif lookup.LookupType != 2:
                    continue
                glyphs = sub.Coverage.glyphs
                subtables.append((sub, {g: i for i, g in enumerate(glyphs)}))
        return subtables

    def kern(self, left, right):
        key = (left, right)
        if key not in self.kern_cache:
            value = 0
            for sub, coverage in self.pairs:
                if left not in coverage:
                    continue
                if sub.Format == 1:
                    pairs = sub.PairSet[coverage[left]].PairValueRecord
                    record = next((r for r in pairs if r.SecondGlyph == right), None)
                    if record is None:
                        continue
                    value = getattr(record.Value1, "XAdvance", 0) or 0
                else:
                    first = sub.ClassDef1.classDefs.get(left, 0)
                    second = sub.ClassDef2.classDefs.get(right, 0)
                    record = sub.Class1Record[first].Class2Record[second]
                    value = getattr(record.Value1, "XAdvance", 0) or 0
                break
            self.kern_cache[key] = value
        return self.kern_cache[key]

    def layout(self, text, size, tracking=0.0):
        """(glyph name, x) for each character and the run's width, in px."""
        scale = size / self.upm
        x, previous, placed = 0.0, None, []
        for char in text:
            name = self.cmap.get(ord(char))
            if name is None:
                sys.exit(f"{self.name} has no glyph for {char!r}")
            if previous is not None:
                x += self.kern(previous, name) * scale
            placed.append((name, x))
            x += self.advance[name] * scale + tracking * size
            previous = name
        return placed, (x - tracking * size) if text else 0.0

    def width(self, text, size, tracking=0.0):
        return self.layout(text, size, tracking)[1]

    def spans(self, parts, size, x, y, tracking=0.0, anchor="start"):
        """Outlines for [(text, fill), ...] set as one kerned run."""
        text = "".join(part for part, _ in parts)
        placed, width = self.layout(text, size, tracking)
        if anchor == "end":
            x -= width
        elif anchor == "middle":
            x -= width / 2
        scale = size / self.upm
        out, cursor = [], 0
        for part, fill in parts:
            pen = SVGPathPen(self.glyphs, ntos=_num)
            for name, gx in placed[cursor : cursor + len(part)]:
                self.glyphs[name].draw(
                    TransformPen(pen, (scale, 0, 0, -scale, x + gx, y))
                )
            cursor += len(part)
            commands = pen.getCommands()
            if commands:
                out.append(f'<path fill="{fill}" d="{commands}"/>')
        return "".join(out)

    def text(self, text, size, x, y, fill, tracking=0.0, anchor="start"):
        return self.spans([(text, fill)], size, x, y, tracking, anchor)

    def wrap(self, text, size, max_width):
        lines, line = [], ""
        for word in text.split():
            candidate = f"{line} {word}".strip()
            if line and self.width(candidate, size) > max_width:
                lines.append(line)
                line = word
            else:
                line = candidate
        return lines + ([line] if line else [])


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

# One new thing at a time, slow sine easing (brand motion). `both` keeps every
# element visible wherever SVG animation doesn't run.
RISE = """<style>
.r{animation:rise 0.9s cubic-bezier(0.37,0,0.63,1) both}
.d1{animation-delay:0.15s}.d2{animation-delay:0.45s}.d3{animation-delay:0.75s}
.d4{animation-delay:1.05s}.d5{animation-delay:1.35s}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.r{animation:none}}
</style>"""


def document(width, height, label, body, style=""):
    label = html.escape(label)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(width)}" '
        f'height="{_num(height)}" viewBox="0 0 {_num(width)} {_num(height)}" '
        f'role="img" aria-label="{label}"><title>{label}</title>{style}{body}</svg>\n'
    )


def panel(width, height, fill, stroke, radius):
    return (
        f'<rect x="0.5" y="0.5" width="{_num(width - 1)}" height="{_num(height - 1)}" '
        f'rx="{radius}" fill="{fill}" stroke="{stroke}"/>'
    )


def write(name, theme, svg):
    path = ASSETS / f"{name}-{theme}.svg"
    path.write_text(svg)
    return path


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------


def banner(f, theme, c):
    w, h, pad = 1200, 440, 64
    head = [
        f'<g class="r d1">'
        + f["display"].spans(
            [("amin", c["text"]), ("_", c["accent"]), ("decods", c["text"])],
            30, pad, 92, tracking=-0.02,
        )
        + f["mono"].text(SITE, 14, w - pad, 88, c["muted"], tracking=0.02, anchor="end")
        + "</g>",
        f'<g class="r d2">'
        + f["mono_medium"].text(
            "FULL-STACK SOFTWARE ENGINEER", 15, pad, 178, c["accent"], tracking=0.16
        )
        + "</g>",
        f'<g class="r d3">'
        + f["display"].text("Building software.", 68, pad, 250, c["text"], tracking=-0.025)
        + "</g>",
        f'<g class="r d4">'
        + f["display"].text("Decoding technology.", 68, pad, 322, c["accent"], tracking=-0.025)
        + "</g>",
        f'<g class="r d5">'
        + f["body"].text(
            "Practical videos on full-stack development, AI and new technology,",
            20, pad, 378, c["muted"],
        )
        + f["body"].text(
            "backed by 6+ years of product engineering experience.", 20, pad, 406, c["muted"]
        )
        + "</g>",
    ]
    body = panel(w, h, c["ground"], c["line"], 18) + "".join(head)
    label = "Amin Keshavarzi, full-stack software engineer. Building software. Decoding technology."
    return document(w, h, label, body, RISE)


BUTTONS = [
    ("youtube", "YouTube", HANDLE),
    ("instagram", "Instagram", HANDLE),
    ("tiktok", "TikTok", "@akeshavarzi_dev"),
    ("x", "X", HANDLE),
    ("threads", "Threads", HANDLE),
    ("linkedin", "LinkedIn", "amin-keshavarzi"),
    ("website", "Website", SITE),
]


def button(f, c, label, handle, primary):
    w, h = 190, 60
    fill, ink, sub = (
        (c["accent"], c["on_accent"], c["on_accent"])
        if primary
        else (c["surface"], c["text"], c["muted"])
    )
    stroke = c["accent"] if primary else c["line"]
    body = (
        panel(w, h, fill, stroke, 12)
        + f["display_semibold"].text(label, 17, 18, 27, ink, tracking=-0.01)
        + f["mono"].text(handle, 12, 18, 45, sub)
        + f["mono"].text("↗", 15, w - 18, 35, sub, anchor="end")
    )
    return document(w, h, f"{label} {handle}", body)


SECTIONS = [
    ("videos", "01", "Latest videos"),
    ("work", "02", "Selected work"),
    ("toolbox", "03", "Toolbox"),
]


def heading(f, c, number, title):
    title_width = f["display"].width(title, 30, -0.02)
    w, h = max(title_width, 120) + 4, 66
    rule_x = f["mono_medium"].width(number, 13, 0.2) + 10
    body = (
        f["mono_medium"].text(number, 13, 1, 18, c["accent"], tracking=0.2)
        + f'<rect x="{_num(rule_x)}" y="12" width="28" height="1.5" fill="{c["accent"]}" opacity="0.6"/>'
        + f["display"].text(title, 30, 0, 56, c["text"], tracking=-0.02)
    )
    return document(w, h, title, body)


PROJECTS = [
    (
        "circulearn",
        "CircuLearn",
        "circulearn.co.uk",
        "My MSc project, now open source. A logic-circuit simulator used in teaching and featured on the React Flow showcase.",
        "Next.js · React Flow · Node.js · Postgres · Redis",
    ),
    (
        "diba-delights",
        "Diba Delights",
        "diba-delights.co.uk",
        "Storefront for a Chester bakery, built end to end: menu, dietary filters, cart and accounts.",
        "Next.js · TypeScript · Tailwind · Neon · Better Auth",
    ),
    (
        "bridge-st-coffee",
        "Bridge St Coffee",
        "bridgestcoffee.co.uk",
        "Site for a Chester coffee shop, from the menu and gallery to where to find them.",
        "Next.js · TypeScript · Tailwind · Neon · Redis",
    ),
    (
        "portfolio",
        "Portfolio and videos",
        SITE,
        "Where my work and videos live. New YouTube uploads show up there on their own.",
        "Next.js 16 · TypeScript · Tailwind · Motion",
    ),
]


def card(f, c, slug, title, domain, description, tech):
    w, pad = 560, 24
    shot_h = (w - 2) * 10 / 16
    image = base64.b64encode((SOURCES / f"{slug}.jpg").read_bytes()).decode()
    lines = f["body"].wrap(description, 15, w - 2 * pad)[:3]
    y = shot_h + 44
    text = f["display_semibold"].text(title, 24, pad, y, c["text"], tracking=-0.015)
    text += f["mono"].text(f"{domain} ↗", 12, w - pad, y - 2, c["muted"], anchor="end")
    for i, line in enumerate(lines):
        text += f["body"].text(line, 15, pad, y + 30 + i * 22, c["muted"])
    tech_y = y + 30 + len(lines) * 22 + 16
    text += f["mono"].text(tech, 12, pad, tech_y, c["accent"])
    h = tech_y + 26
    clip = (
        f'<clipPath id="shot"><path d="M1 17A16 16 0 0 1 17 1H{_num(w - 17)}'
        f'A16 16 0 0 1 {_num(w - 1)} 17V{_num(shot_h + 1)}H1Z"/></clipPath>'
    )
    body = (
        panel(w, h, c["surface"], c["line"], 16)
        + f"<defs>{clip}</defs>"
        + f'<image clip-path="url(#shot)" x="1" y="1" width="{_num(w - 2)}" '
        f'height="{_num(shot_h)}" preserveAspectRatio="xMidYMid slice" '
        f'href="data:image/jpeg;base64,{image}"/>'
        + f'<rect x="1" y="{_num(shot_h + 1)}" width="{_num(w - 2)}" height="1" fill="{c["line"]}"/>'
        + text
    )
    return document(w, h, f"{title}: {description}", body)


TOOLBOX = [
    ("Frontend", "React · Next.js · TypeScript · JavaScript · Vue.js · Nuxt"),
    ("Styling & UI", "Tailwind CSS · SCSS · shadcn/ui · Material-UI"),
    ("Backend & data", "Node.js · Express · PostgreSQL (Neon) · Redis · Drizzle · Prisma · Better Auth"),
    ("AI engineering", "LLM integrations · Agent harnesses · Skills · System prompts · Evals · RAG"),
    ("Testing", "Playwright · Vitest · Jest · Cypress · Storybook"),
    ("Cloud & DevOps", "Vercel · AWS · Azure · DigitalOcean · Docker · Nginx · GitHub Actions"),
]


def toolbox(f, c):
    pad, label_w, row_h, size, sep = 32, 220, 48, 17, "  ·  "
    # Same 1200 width as the banner and footer, so all three scale alike;
    # wider only if a row would otherwise run off the card.
    widest = max(f["body"].width(items.replace(" · ", sep), size) for _, items in TOOLBOX)
    w = max(1200, pad * 2 + label_w + widest)
    h = pad * 2 + row_h * len(TOOLBOX) - 14
    body = panel(w, h, c["surface"], c["line"], 16)
    for i, (label, items) in enumerate(TOOLBOX):
        y = pad + 20 + i * row_h
        body += f["mono_medium"].text(label.upper(), 13, pad, y - 1, c["muted"], tracking=0.14)
        parts = []
        for j, item in enumerate(items.split(" · ")):
            if j:
                parts.append((sep, c["accent"]))
            parts.append((item, c["text"]))
        body += f["body"].spans(parts, size, pad + label_w, y)
        if i:
            body += f'<rect x="{pad}" y="{_num(y - 30)}" width="{_num(w - 2 * pad)}" height="1" fill="{c["line"]}"/>'
    label = "Toolbox. " + ". ".join(f"{a}: {b}" for a, b in TOOLBOX)
    return document(w, h, label, body)


def footer(f, c):
    w, h, pad = 1200, 112, 36
    body = (
        panel(w, h, c["surface"], c["line"], 16)
        + f["display"].spans([("AK", c["text"]), (".", c["accent"])], 40, pad, 70, tracking=-0.03)
        + f["body"].spans(
            [("Find me as ", c["muted"]), (HANDLE, c["text"]), (" everywhere", c["muted"])],
            18, pad + 110, 63,
        )
        + f["mono_medium"].text(f"{SITE}/connect ↗", 14, w - pad, 62, c["accent"], anchor="end")
    )
    return document(w, h, f"AK. Find me as {HANDLE} everywhere: {SITE}/connect", body)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fonts", action="append", required=True, type=Path)
    args = parser.parse_args()

    def find(name):
        for folder in args.fonts:
            if (folder / name).exists():
                return folder / name
        sys.exit(f"{name} not found in {', '.join(map(str, args.fonts))}")

    grotesk = find("SpaceGrotesk-Variable.ttf")
    inter = find("Inter-Variable.ttf")
    mono = find("GeistMono-Variable.woff2")
    faces = {
        "display": Face(grotesk, wght=700),
        "display_semibold": Face(grotesk, wght=600),
        "body": Face(inter, wght=400, opsz=14),
        "mono": Face(mono, wght=400),
        "mono_medium": Face(mono, wght=500),
    }

    written = []
    for theme, colours in PALETTES.items():
        written.append(write("banner", theme, banner(faces, theme, colours)))
        for slug, label, handle in BUTTONS:
            written.append(
                write(f"btn-{slug}", theme, button(faces, colours, label, handle, slug == "youtube"))
            )
        for slug, number, title in SECTIONS:
            written.append(write(f"h-{slug}", theme, heading(faces, colours, number, title)))
        for project in PROJECTS:
            written.append(write(f"card-{project[0]}", theme, card(faces, colours, *project)))
        written.append(write("toolbox", theme, toolbox(faces, colours)))
        written.append(write("footer", theme, footer(faces, colours)))

    for path in written:
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
