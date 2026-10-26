"""Capture full-page screenshots of the running app at the required widths, and
report any page that scrolls horizontally.

Usage: python scripts/screenshots.py OUT_DIR [BASE_URL] [PATH ...]

Dev-only tool (needs `pip install playwright`; uses the locally installed Chrome).
"""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

WIDTHS = (320, 390, 768, 1280, 1440)
DEFAULT_PATHS = (
    "/",
    "/shop/",
    "/shop/fabrics/",
    "/product/cushion-cover-g-3/",
    "/product/kente-print-101024/",
    "/cart/",
)


def main(out_dir: str, base: str = "http://127.0.0.1:5055", *paths: str) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    problems = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for width in WIDTHS:
            page = browser.new_page(viewport={"width": width, "height": 900})
            # Keep the newsletter pop-up out of layout screenshots.
            page.add_init_script(
                "try { localStorage.setItem('gg-newsletter-seen', '1') } catch (e) {}"
            )
            for path in paths or DEFAULT_PATHS:
                page.goto(base + path, wait_until="networkidle")
                # Scroll through the page (and any carousels) so lazy images load.
                page.evaluate(
                    """async () => {
                        for (let y = 0; y < document.body.scrollHeight; y += 600) {
                            window.scrollTo({ top: y, behavior: 'instant' });
                            await new Promise(r => setTimeout(r, 60));
                        }
                        document.querySelectorAll('img[loading=lazy]')
                            .forEach(i => i.loading = 'eager');
                        window.scrollTo({ top: 0, behavior: 'instant' });
                    }"""
                )
                page.wait_for_load_state("networkidle")
                name = path.strip("/").replace("/", "_") or "root"
                page.screenshot(path=out / f"{name}-{width}.png", full_page=True)
                scroll_width = page.evaluate("document.documentElement.scrollWidth")
                flag = "" if scroll_width <= width else f"  HORIZONTAL SCROLL ({scroll_width}px)"
                problems += bool(flag)
                print(f"{width:>5} {path}{flag}")
            page.close()
        browser.close()
    return 1 if problems else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
