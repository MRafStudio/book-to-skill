#!/usr/bin/env python3
"""Run the fetch cascade for one URL and render a review dashboard.

Usage:
  python tools/dashboard.py https://example.com/docs
  python tools/dashboard.py <url> --force trafilatura

Writes dashboard/render.html and prints its path. The page is self-contained
(data inlined, no network, no build step) so Hermes can show it in the chat
with a ::preview directive.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from book_to_skill.fetcher import Fetcher  # noqa: E402

DEFAULT_CATEGORY = "software-development"


def discover_categories() -> list:
    """Categories = subdirectories of the active Hermes profile's skills root."""
    home = os.environ.get("HERMES_HOME")
    roots = []
    if home:
        roots.append(Path(home) / "skills")
    roots.append(Path.home() / ".hermes" / "skills")
    for root in roots:
        if root.is_dir():
            names = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
            if names:
                return names
    return [DEFAULT_CATEGORY]


def engine_info() -> dict:
    """What the deterministic half of the pipeline can actually do right now."""
    def has(module: str) -> bool:
        try:
            __import__(module)
            return True
        except ImportError:
            return False

    return {
        "python": sys.version.split()[0],
        "trafilatura": has("trafilatura"),
        "beautifulsoup4": has("bs4"),
        "docling": has("docling"),
        "pdftotext": bool(shutil.which("pdftotext")),
        "calibre": bool(shutil.which("ebook-convert")),
        "hermes_home": os.environ.get("HERMES_HOME", ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="book-to-skill (Hermes fork) -- review dashboard")
    parser.add_argument("url")
    parser.add_argument("--out", default=str(REPO / "b2s_fetched"))
    parser.add_argument("--force", default=None, choices=[None, "raw-md", "trafilatura", "bs4", "stdlib"])
    parser.add_argument("--html", default=str(REPO / "dashboard" / "render.html"))
    args = parser.parse_args()

    fetcher = Fetcher(force=args.force)
    report = fetcher.fetch_to_dir(args.url, args.out)

    data = {
        "report": report,
        "plugins": [
            {"name": p.NAME, "priority": p.PRIORITY, "score": p.matches(args.url)}
            for p in fetcher.plugins
        ],
        "categories": discover_categories(),
        "engine": engine_info(),
        "default_category": DEFAULT_CATEGORY,
    }

    template = (REPO / "dashboard" / "widget.html").read_text(encoding="utf-8")
    html = template.replace("__B2S_DATA__", json.dumps(data, ensure_ascii=False))
    out_html = Path(args.html)
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")
    print(str(out_html))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
