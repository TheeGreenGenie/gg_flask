"""Snapshot every URL advertised by the public WordPress sitemap."""

from __future__ import annotations

import argparse
import csv
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree

ORIGIN = "https://ggafrikaneleganz.com"
INDEX = f"{ORIGIN}/wp-sitemap.xml"
NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def fetch(url: str) -> ElementTree.Element:
    if urllib.parse.urlsplit(url).netloc != "ggafrikaneleganz.com":
        raise ValueError("Sitemap attempted to leave the expected public host")
    request = urllib.request.Request(url, headers={"User-Agent": "GG-Rebuild-Inventory/1.0"})  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return ElementTree.fromstring(response.read())  # noqa: S314


def crawl() -> list[dict[str, str]]:
    index = fetch(INDEX)
    rows: list[dict[str, str]] = []
    for location in index.findall("s:sitemap/s:loc", NS):
        sitemap_url = location.text or ""
        kind = Path(urllib.parse.urlsplit(sitemap_url).path).stem.removeprefix("wp-sitemap-")
        sitemap = fetch(sitemap_url)
        for node in sitemap.findall("s:url", NS):
            loc = node.findtext("s:loc", default="", namespaces=NS)
            lastmod = node.findtext("s:lastmod", default="", namespaces=NS)
            rows.append(
                {
                    "path": urllib.parse.urlsplit(loc).path,
                    "type": kind,
                    "last_modified": lastmod,
                    "source_sitemap": sitemap_url,
                }
            )
    return sorted(rows, key=lambda row: row["path"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("live_url_inventory.csv"))
    args = parser.parse_args()
    rows = crawl()
    with args.output.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} URLs to {args.output}")  # noqa: T201


if __name__ == "__main__":
    main()
