"""Copy selected images from the old WordPress uploads folder into web-sized WebP files
for the Phase 2 design prototypes.

Usage: python scripts/prep_prototype_assets.py C:/xampp/htdocs/mysite/wp-content/uploads

Dev-only tool (needs Pillow). The brand-story video thumbnail (video-story.webp) is
YouTube's hqdefault image for video _wQiEZOHa7s, converted once by hand.
Real catalog media goes through the launch-manifest intake in Phase 3, not this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "img" / "proto"

# output name -> (source path relative to uploads, max width)
IMAGES: dict[str, tuple[str, int]] = {
    "hero": ("2021/09/Slider-N1.jpg", 1920),
    "open-african-prints": ("2024/09/Fabrics-Ankara.jpg", 800),
    "open-bags-shoes": ("2024/07/Bags-and-shoes.jpg", 800),
    "open-custom-sewing": ("2024/07/Custom-Design2.jpg", 800),
    "open-beauty": ("2024/09/Beauty-Lifestyle.jpg", 800),
    "cat-ankara": ("2024/09/Ankara-Category.jpg", 900),
    "cat-kente": ("2024/09/Kente-Print-Category.jpg", 900),
    "cat-ethnic": ("2024/09/Ethnic-Category.jpg", 900),
    "cat-satin": ("2024/09/Satin-category.jpg", 900),
    "cat-by-the-yard": ("2024/09/Ankara-By-The-Yard-Category.jpg", 900),
    "cat-3-yards": ("2024/09/Ankara-3-yards-Category.jpg", 900),
    "cat-6-yards": ("2024/09/Ankara-6-Yards-Category.jpg", 900),
    "cat-pre-cuts": ("2024/05/Pre-Cut.jpg", 900),
    "cat-classic": ("2024/09/Classic-Category.jpg", 900),
    "cat-floral": ("2024/09/Floral-Category.jpg", 900),
    "cat-final-cut": ("2024/09/Final-Cut-Category.jpg", 900),
    "cat-ankara-plus": ("2024/09/Ankara-Plus-Category.jpg", 900),
    "cat-reversible-jacket": ("2024/10/Reversible-Jackets.jpg", 900),
    "cat-sarong": ("2024/10/Sarong-Wrap.jpg", 900),
    "cat-adire": ("2025/01/Adire-Category.jpg", 900),
    "post-ankara": ("2024/10/Ankara-Blog-Post1.jpg", 1200),
    "post-promo": ("2024/10/Promo.jpg", 1200),
    "p-wrap-skirt": ("2024/10/Wrap-Skirt-WR-2002-101196.jpg", 1000),
    "p-ankara-101025": ("2024/07/African-Ankara-Print-101025.jpg", 1000),
    "p-ankara-101025-1": ("2024/07/African-Ankara-Print-101025-1.jpg", 1000),
    "p-ankara-101025-2": ("2024/07/African-Ankara-Print-101025-2.jpg", 1000),
    "p-ankara-101025-3": ("2024/07/African-Ankara-Print-101025-3.jpg", 1000),
    "p-ankara-101025-4": ("2024/07/African-Ankara-Print-101025-4.jpg", 1000),
    "p-ankara-101025-5": ("2024/07/African-Ankara-Print-101025-5.jpg", 1000),
    "p-ankara-101054": ("2024/07/African-Ankara-Print-101054-B1.jpg", 1000),
    "p-ankara-101068": ("2024/07/African-Ankara-Print-101068-O1Y.jpg", 1000),
    "p-ankara-101066": ("2024/07/African-Ankara-Print-101066-O1.jpg", 1000),
    "p-cushion-g3": ("2023/01/Cushion-Covers-Group-3.jpg", 1000),
    "p-cushion-g4": ("2023/01/Cushion-Covers-Group-4.jpg", 1000),
    "p-adire-101219": ("2025/01/Adire-Print-101219.jpg", 1000),
    "p-adire-101218": ("2025/01/Adire-Print-101218.jpg", 1000),
    "p-adire-101217": ("2025/01/Adire-Print-101217.jpg", 1000),
    "p-adire-101216": ("2025/01/Adire-Print-101216.jpg", 1000),
}


def convert(src: Path, dest: Path, max_width: int) -> None:
    with Image.open(src) as im:
        im = im.convert("RGB")
        if im.width > max_width:
            im = im.resize((max_width, round(im.height * max_width / im.width)), Image.LANCZOS)
        im.save(dest, "WEBP", quality=80, method=6)


def main(uploads: str) -> int:
    root = Path(uploads)
    OUT.mkdir(parents=True, exist_ok=True)
    missing = []
    for name, (rel, width) in IMAGES.items():
        src = root / rel
        if not src.is_file():
            missing.append(rel)
            continue
        dest = OUT / f"{name}.webp"
        convert(src, dest, width)
        print(f"{dest.name:32} {dest.stat().st_size // 1024:>5} KB")
    for rel in missing:
        print(f"MISSING {rel}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1]))
