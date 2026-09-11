#!/usr/bin/env python3
"""Fetch a URL into a local source file for the upstream extract.py pipeline.

Usage:
  python tools/fetch_preview.py https://example.com/docs
  python tools/fetch_preview.py <url> --force trafilatura|bs4|stdlib|raw-md
  python tools/fetch_preview.py <url> --json
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# UTF-8 stdout/stderr: the report uses ✓ / ✗ and the page titles are not ASCII.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from book_to_skill.fetcher import Fetcher  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="book-to-skill (Hermes fork) -- URL to local source file"
    )
    parser.add_argument("url")
    parser.add_argument(
        "--out",
        default=str(REPO / "b2s_fetched"),
        help="output directory (default: <repo>/b2s_fetched)",
    )
    parser.add_argument(
        "--force",
        default=None,
        choices=[None, "raw-md", "trafilatura", "bs4", "stdlib"],
        help="narrow the cascade to one strategy instead of running auto",
    )
    parser.add_argument("--json", action="store_true", help="print the raw report")
    args = parser.parse_args()

    report = Fetcher(force=args.force).fetch_to_dir(args.url, args.out)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    print(f"URL       : {report['url']}")
    print("Стратегии :")
    for attempt in report["attempts"]:
        mark = "✓" if attempt["ok"] else "✗"
        print(f"  {mark} {attempt['plugin']:<20} score={attempt['score']:<4} {attempt['note']}")

    if not report.get("ok"):
        print("\nFAILED: ни одна стратегия не дала текста")
        return 1

    print(f"\nПобедила  : {report['strategy']} ({report['kind']})")
    print(f"Title     : {report['title']}")
    print(f"Объём     : {report['chars']} симв · {report['words']} слов · ~{report['est_tokens']} токенов")
    print(f"Мусор сайта: {report['junk_total']} вхождений {report['junk'] or ''}")
    print(f"Файл      : {report['source_file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
