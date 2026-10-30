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
.d4{animation-delay:1.05s}
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
# Assets: kept compact so the whole profile reads in one or two screens.
# ---------------------------------------------------------------------------


def banner(f, c):
    w, h, pad = 1200, 280, 56
    groups = [
        f["display"].spans(
            [("amin", c["text"]), ("_", c["accent"]), ("decods", c["text"])],
            26, pad, 70, tracking=-0.02,
        )
        + f["mono"].text(SITE, 13, w - pad, 66, c["muted"], tracking=0.02, anchor="end"),
        f["mono_medium"].text(
            "FULL-STACK SOFTWARE ENGINEER", 14, pad, 138, c["accent"], tracking=0.16
        ),
        f["display"].spans(
            [("Building software. ", c["text"]), ("Decoding technology.", c["accent"])],
            52, pad, 198, tracking=-0.025,
        ),
        f["body"].text(
            "Practical videos on full-stack development, AI and new technology.",
            19, pad, 240, c["muted"],
        ),
    ]
    body = panel(w, h, c["ground"], c["line"], 18) + "".join(
        f'<g class="r d{i + 1}">{group}</g>' for i, group in enumerate(groups)
    )
    label = "Amin Keshavarzi, full-stack software engineer. Building software. Decoding technology."
    return document(w, h, label, body, RISE)


BUTTONS = [
    ("youtube", "YouTube"),
    ("instagram", "Instagram"),
    ("tiktok", "TikTok"),
    ("x", "X"),
    ("threads", "Threads"),
    ("linkedin", "LinkedIn"),
    ("website", "Website"),
]


def button(f, c, label, primary):
    size, pad, h = 15, 14, 36
    arrow_gap, arrow_w = 8, f["mono"].width("↗", 13)
    w = pad * 2 + f["display_semibold"].width(label, size, -0.01) + arrow_gap + arrow_w
    fill, ink, sub = (
        (c["accent"], c["on_accent"], c["on_accent"])
        if primary
        else (c["surface"], c["text"], c["muted"])
    )
    body = (
        panel(w, h, fill, c["accent"] if primary else c["line"], 10)
        + f["display_semibold"].text(label, size, pad, 23.5, ink, tracking=-0.01)
        + f["mono"].text("↗", 13, w - pad, 23, sub, anchor="end")
    )
    return document(w, h, label, body)


SECTIONS = [
    ("videos", "01", "LATEST VIDEOS"),
    ("work", "02", "SELECTED WORK"),
]


def kicker(f, c, number, title):
    """The site's section eyebrow: number, short rule, label."""
    size, tracking = 13, 0.16
    number_w = f["mono_medium"].width(number, size, tracking)
    rule_x = number_w + 10
    title_x = rule_x + 24 + 10
    w = title_x + f["mono_medium"].width(title, size, tracking) + 2
    body = (
        f["mono_medium"].text(number, size, 0, 15, c["accent"], tracking=tracking)
        + f'<rect x="{_num(rule_x)}" y="9.5" width="24" height="1.5" fill="{c["accent"]}" opacity="0.6"/>'
        + f["mono_medium"].text(title, size, title_x, 15, c["text"], tracking=tracking)
    )
    return document(w, 20, title.title(), body)


PROJECTS = [
    (
        "circulearn",
        "CircuLearn",
        "My open-source MSc project, used in teaching and on the React Flow showcase.",
        "Next.js · React Flow · Node.js · Postgres",
    ),
    (
        "diba-delights",
        "Diba Delights",
        "Storefront for a Chester bakery: menu, dietary filters, cart and accounts.",
        "Next.js · TypeScript · Neon · Better Auth",
    ),
    (
        "bridge-st-coffee",
        "Bridge St Coffee",
        "Site for a Chester coffee shop: menu, gallery and how to find them.",
        "Next.js · TypeScript · Tailwind · Neon",
    ),
    (
        "portfolio",
        "Portfolio and videos",
        "Where my work and videos live. New uploads show up there on their own.",
        "Next.js 16 · TypeScript · Tailwind",
    ),
]


def card(f, c, slug, title, description, tech):
    w, h, inset = 560, 144, 12
    thumb_w, thumb_h = 192, 120
    image = base64.b64encode((SOURCES / f"{slug}.jpg").read_bytes()).decode()
    x = inset + thumb_w + 18
    lines = f["body"].wrap(description, 14.5, w - x - 18)
    if len(lines) > 2:
        sys.exit(f"{title}: description needs {len(lines)} lines, the card fits 2. Shorten it.")
    text = f["display_semibold"].text(title, 20, x, 40, c["text"], tracking=-0.015)
    text += f["mono"].text("↗", 13, w - 18, 38, c["muted"], anchor="end")
    for i, line in enumerate(lines):
        text += f["body"].text(line, 14.5, x, 67 + i * 20.5, c["muted"])
    text += f["mono"].text(tech, 12, x, 122, c["accent"])
    body = (
        panel(w, h, c["surface"], c["line"], 14)
        + f'<defs><clipPath id="thumb"><rect x="{inset}" y="{inset}" '
        f'width="{thumb_w}" height="{thumb_h}" rx="8"/></clipPath></defs>'
        + f'<image clip-path="url(#thumb)" x="{inset}" y="{inset}" width="{thumb_w}" '
        f'height="{thumb_h}" preserveAspectRatio="xMidYMin slice" '
        f'href="data:image/jpeg;base64,{image}"/>'
        + f'<rect x="{inset + 0.5}" y="{inset + 0.5}" width="{thumb_w - 1}" '
        f'height="{thumb_h - 1}" rx="8" fill="none" stroke="{c["line"]}"/>'
        + text
    )
    return document(w, h, f"{title}: {description}", body)


TOOLBOX = (
    "React · Next.js · TypeScript · Tailwind CSS · shadcn/ui · Node.js · Express · "
    "PostgreSQL · Redis · Drizzle · Better Auth · LLM integrations · Agents · RAG · "
    "Playwright · Vitest · Vercel · Docker · AWS · Azure"
)


def toolbox(f, c):
    w, pad, label_w, size, sep, line_h = 1200, 32, 150, 18, "  ·  ", 34
    items = TOOLBOX.split(" · ")
    max_w = w - pad * 2 - label_w
    rows, row = [], []
    for item in items:
        candidate = sep.join(row + [item])
        if row and f["body"].width(candidate, size) > max_w:
            rows.append(row)
            row = [item]
        else:
            row.append(item)
    rows.append(row)
    h = pad * 2 + line_h * (len(rows) - 1) + 12
    body = panel(w, h, c["surface"], c["line"], 14)
    body += f["mono_medium"].text("TOOLBOX", 13, pad, pad + 11, c["muted"], tracking=0.16)
    for i, row in enumerate(rows):
        parts = []
        for j, item in enumerate(row):
            if j:
                parts.append((sep, c["accent"]))
            parts.append((item, c["text"]))
        body += f["body"].spans(parts, size, pad + label_w, pad + 12 + i * line_h)
    return document(w, h, "Toolbox: " + ", ".join(items), body)


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

    # Start clean so renamed or retired images don't linger.
    for old in ASSETS.glob("*.svg"):
        old.unlink()

    written = []
    for theme, colours in PALETTES.items():
        written.append(write("banner", theme, banner(faces, colours)))
        for slug, label in BUTTONS:
            written.append(
                write(f"btn-{slug}", theme, button(faces, colours, label, slug == "youtube"))
            )
        for slug, number, title in SECTIONS:
            written.append(write(f"h-{slug}", theme, kicker(faces, colours, number, title)))
        for project in PROJECTS:
            written.append(write(f"card-{project[0]}", theme, card(faces, colours, *project)))
        written.append(write("toolbox", theme, toolbox(faces, colours)))

    total = sum(path.stat().st_size for path in written) / 1024
    for path in written:
        print(f"{path.relative_to(ROOT)}  {path.stat().st_size / 1024:.1f} KB")
    print(f"{len(written)} files, {total:.0f} KB")


if __name__ == "__main__":
    main()
