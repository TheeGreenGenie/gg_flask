"""Automatic product naming from the print family and its colors.

The family comes from the name the product already has, exactly as the website shows it
("African Ankara Print - 101025" -> family "African Ankara Print", code "101025"), with a
category-based fallback. Colors come from the manifest's `colors` list when given, or are
detected from the cover photo and snapped to a fixed palette so names and filters stay
consistent.

    African Ankara Print - Purple & Blue - 101025
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

# Controlled color vocabulary (also the future color filter, D-007). Anchor RGB values.
PALETTE: dict[str, tuple[int, int, int]] = {
    "Black": (20, 20, 20),
    "White": (245, 245, 240),
    "Cream": (236, 222, 190),
    "Grey": (128, 128, 128),
    "Brown": (110, 70, 40),
    "Tan": (190, 150, 100),
    "Red": (200, 30, 35),
    "Burgundy": (120, 20, 40),
    "Orange": (240, 120, 30),
    "Yellow": (250, 210, 40),
    "Gold": (200, 155, 40),
    "Green": (40, 150, 60),
    "Olive": (110, 115, 40),
    "Teal": (20, 140, 140),
    "Blue": (30, 90, 200),
    "Navy": (20, 35, 90),
    "Purple": (110, 50, 150),
    "Pink": (235, 100, 160),
}
COLOR_NAMES = frozenset(PALETTE) | {"Multicolor"}
NEUTRALS = frozenset({"Black", "White", "Grey"})
NEUTRAL_CHROMA = 10.0  # Lab chroma below this counts as black/white/grey
MULTI_SHARE = 0.08
NEUTRAL_SHARE = 0.35

# Fallback family label per category when the existing name has no "Family - code" shape.
FAMILY_BY_CATEGORY = {
    "ankara": "African Ankara Print",
    "kente": "Kente Print",
    "adire": "Adire Print",
    "satin-chiffon": "Satin/Chiffon",
    "ethnic-prints": "Ethnic Print",
    "skirts": "Wrap Skirt",
    "jackets": "Reversible Jacket",
    "cushion-covers": "Cushion Cover",
    "sarong-wraps": "Sarong Wrap",
}

MAX_COLORS = 2
MIN_SHARE = 0.10  # a color must cover at least 10% of the item to be named
CODE_RE = re.compile(r"^[A-Z]{0,4}[- ]?\d[\w-]*$")


@dataclass(frozen=True)
class NameParts:
    family: str
    code: str


def split_name(name: str, category: str | None = None) -> NameParts:
    """'African Ankara Print - 101025' -> ('African Ankara Print', '101025').

    Already auto-named values ('Family - Colors - Code') are recognised so the namer can
    run repeatedly without stacking colors.
    """
    parts = [p.strip() for p in re.split(r"\s+[-–]\s*", name.strip()) if p.strip()]
    if len(parts) >= 2 and CODE_RE.match(parts[-1]):
        return NameParts(parts[0], parts[-1])
    # 'Cushion Cover G-3' -> ('Cushion Cover', 'G-3')
    tail = re.match(r"^(.*?)\s+([A-Z]{1,4}-?\d[\w-]*|\d{3,}[\w-]*)$", parts[0]) if parts else None
    if len(parts) == 1 and tail:
        return NameParts(tail.group(1), tail.group(2))
    fallback = FAMILY_BY_CATEGORY.get(category or "", "")
    return NameParts(parts[0] if parts else fallback, "")


def compose(family: str, colors: list[str], code: str) -> str:
    color_text = " & ".join(colors[:MAX_COLORS])
    return " - ".join(p for p in (family, color_text, code) if p)


@dataclass(frozen=True)
class ColorAnalysis:
    """Color shares measured over fabric pixels only (background removed first)."""

    shares: list[tuple[str, float]]  # (palette name, fraction of fabric pixels), largest first
    fabric_pixels: int
    background_pixels: int

    @property
    def background_fraction(self) -> float:
        total = self.fabric_pixels + self.background_pixels
        return self.background_pixels / total if total else 0.0

    def names(self, limit: int = MAX_COLORS) -> list[str]:
        """Colors for the product name, following the naming rules."""
        if not self.shares:
            return []
        strong = [n for n, share in self.shares if share >= MULTI_SHARE and n not in NEUTRALS]
        if len(strong) >= 5 and self.shares[0][1] < 0.25:
            return ["Multicolor"]
        # Prefer real colors; a neutral is only named when it covers a large part of the item.
        named = [
            n
            for n, share in self.shares
            if share >= MIN_SHARE and (n not in NEUTRALS or share >= NEUTRAL_SHARE)
        ]
        return named[:limit] or [self.shares[0][0]]

    def describe(self, top: int = 5) -> str:
        parts = [f"{n} {share:.0%}" for n, share in self.shares[:top]]
        return ", ".join(parts) + f"  (background removed: {self.background_fraction:.0%})"


def detect_colors(image_path: Path, limit: int = MAX_COLORS) -> list[str]:
    """Dominant palette colors of the fabric in a photo, most prominent first."""
    return analyze(image_path).names(limit)


def analyze(image_path: Path) -> ColorAnalysis:
    """Measure the fabric's colors as a share of fabric pixels.

    1. Sample the photo without blending (nearest neighbour), so small print details keep
       their true colors instead of averaging into muddy browns.
    2. Find the backdrop: if the photo's edge is mostly one plain low-chroma color,
       flood-fill inward from the edge across pixels close to that color. Only background
       connected to the edge is removed, so white inside the fabric still counts.
    3. Match every remaining pixel to the palette and count.
    """
    im, mask = _fabric_mask(image_path)
    pixels = _pixels(im)
    counts: Counter[str] = Counter(
        _nearest_cached(px) for px, is_bg in zip(pixels, mask, strict=True) if not is_bg
    )
    fabric = sum(counts.values())
    shares = [(n, c / fabric) for n, c in counts.most_common()] if fabric else []
    return ColorAnalysis(shares, fabric, len(pixels) - fabric)


SAMPLE_WIDTH = 160
BACKGROUND_TOLERANCE = 16.0  # Lab distance from the backdrop color still counted as backdrop
EDGE_UNIFORMITY = 0.55  # share of edge pixels that must match for a backdrop to exist

Lab = tuple[float, float, float]


def _fabric_mask(image_path: Path) -> tuple[Image.Image, list[bool]]:
    """Downsampled image plus a per-pixel background mask (True = background)."""
    with Image.open(image_path) as src:
        im = src.convert("RGB")
    im = im.resize(
        (SAMPLE_WIDTH, max(1, round(SAMPLE_WIDTH * im.height / im.width))),
        Image.Resampling.NEAREST,
    )
    w, h = im.size
    labs = [_to_lab(px) for px in _pixels(im)]
    mask = [False] * (w * h)

    edge = [i for i in range(w * h) if i % w in (0, w - 1) or i // w in (0, h - 1)]
    reference = _backdrop_reference([labs[i] for i in edge])
    if reference is None:
        return im, mask

    def is_bg(i: int) -> bool:
        return _distance(labs[i], reference) <= BACKGROUND_TOLERANCE

    stack = [i for i in edge if is_bg(i)]
    for i in stack:
        mask[i] = True
    while stack:
        i = stack.pop()
        x, y = i % w, i // w
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < w and 0 <= ny < h:
                j = ny * w + nx
                if not mask[j] and is_bg(j):
                    mask[j] = True
                    stack.append(j)

    if mask.count(False) < 0.05 * len(mask):  # removed nearly everything: not a backdrop
        return im, [False] * len(mask)
    return im, mask


def _backdrop_reference(edge_labs: list[Lab]) -> Lab | None:
    """Average Lab color of a plain, low-chroma backdrop around the edge, if there is one."""
    # Studio backdrops are light and plain; a dark edge is the fabric itself (e.g. black prints).
    plain = [lab for lab in edge_labs if lab[0] >= 65 and (lab[1] ** 2 + lab[2] ** 2) ** 0.5 < 18]
    if not plain or len(plain) < EDGE_UNIFORMITY * len(edge_labs):
        return None
    ref: Lab = (
        sum(lab[0] for lab in plain) / len(plain),
        sum(lab[1] for lab in plain) / len(plain),
        sum(lab[2] for lab in plain) / len(plain),
    )
    close = sum(1 for lab in edge_labs if _distance(lab, ref) <= BACKGROUND_TOLERANCE)
    return ref if close >= EDGE_UNIFORMITY * len(edge_labs) else None


def _pixels(im: Image.Image) -> list[tuple[int, int, int]]:
    getter = getattr(im, "get_flattened_data", None) or im.getdata
    return list(getter())


def mask_preview(image_path: Path, width: int = 200) -> Image.Image:
    """The analysed image with the removed background tinted pink, for visual review."""
    im, mask = _fabric_mask(image_path)
    tint = (255, 0, 200)
    preview = Image.new("RGB", im.size)
    preview.putdata(
        [
            ((px[0] + tint[0]) // 2, (px[1] + tint[1]) // 2, (px[2] + tint[2]) // 2) if bg else px
            for px, bg in zip(_pixels(im), mask, strict=True)
        ]
    )
    return preview.resize((width, round(width * preview.height / preview.width)))


def _nearest_cached(rgb: tuple[int, int, int]) -> str:
    # Bucket to 4-bit channels so the Lab search runs at most 4096 times per photo.
    key = (rgb[0] >> 4, rgb[1] >> 4, rgb[2] >> 4)
    name = _BUCKETS.get(key)
    if name is None:
        name = _BUCKETS[key] = nearest_color((key[0] * 16 + 8, key[1] * 16 + 8, key[2] * 16 + 8))
    return name


_BUCKETS: dict[tuple[int, int, int], str] = {}


def nearest_color(rgb: tuple[int, int, int]) -> str:
    """Name a pixel. Colorful pixels only match colorful names (hue matters more than
    lightness), so faded or shadowed fabric is not mistaken for grey."""
    lab = _to_lab(rgb)
    chroma = (lab[1] ** 2 + lab[2] ** 2) ** 0.5
    if chroma < NEUTRAL_CHROMA:
        candidates = [n for n in PALETTE if n in NEUTRALS or n == "Cream"]
        return min(candidates, key=lambda n: abs(lab[0] - _LAB_PALETTE[n][0]))
    candidates = [n for n in PALETTE if n not in NEUTRALS or n == "Cream"]
    return min(candidates, key=lambda n: _distance(lab, _LAB_PALETTE[n], lightness_weight=0.5))


def _to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _distance(
    a: tuple[float, float, float], b: tuple[float, float, float], lightness_weight: float = 1.0
) -> float:
    return (
        (lightness_weight * (a[0] - b[0])) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    ) ** 0.5


_LAB_PALETTE = {name: _to_lab(rgb) for name, rgb in PALETTE.items()}


def review_sheet(
    items: list[tuple[Path, dict[str, Any], ColorAnalysis]], cols: int = 6
) -> Image.Image:
    """Contact sheet: masked photo, proposed colors and pixel shares for each product."""
    cell_w, cell_h = 230, 330
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (path, product, analysis) in enumerate(items):
        x, y = (i % cols) * cell_w, (i // cols) * cell_h
        thumb = mask_preview(path, 210)
        thumb.thumbnail((210, 230))
        sheet.paste(thumb, (x + 10, y + 8))
        lines = [str(product.get("slug", ""))[:34], " & ".join(analysis.names())]
        lines += [f"{n} {share:.0%}" for n, share in analysis.shares[:4]]
        lines.append(f"background removed {analysis.background_fraction:.0%}")
        for j, line in enumerate(lines):
            draw.text((x + 10, y + 244 + j * 13), line, fill="gray" if j == 0 else "black")
    return sheet
