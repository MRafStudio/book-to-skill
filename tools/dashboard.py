#!/usr/bin/env python3
"""Run the fetch cascade for one URL and render a review dashboard.

Usage:
  python tools/dashboard.py https://example.com/docs
  python tools/dashboard.py <url> --force trafilatura
  python tools/dashboard.py <url> --draft-dir staging/my-skill

Writes dashboard/render.html and prints its path. The page is self-contained
(data inlined, no network, no build step) so Hermes can publish it in the
desktop preview pane.
"""
import argparse
import json
import os
import re
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


def active_model() -> str:
    """Which model the host is configured to run.

    Read from the host config without a YAML dependency (the two keys we want sit
    at the top of a single top-level ``model:`` block). This is what the *host*
    is configured for -- a running session can override it, so the value is
    labelled as the configured default, not as a claim about the live session.
    """
    home = os.environ.get("HERMES_HOME")
    cfg = Path(home) / "config.yaml" if home else Path.home() / ".hermes" / "config.yaml"
    if not cfg.is_file():
        return ""
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    model = provider = ""
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^\w", line):  # leave the top-level block
            if model or provider:
                break
            continue
        key, _, value = line.strip().partition(":")
        value = value.strip().strip("'\"")
        if key == "default" and not model:
            model = value
        elif key == "provider" and not provider:
            provider = value
    if model and provider:
        return f"{model} · {provider}"
    return model or provider


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
        "model": active_model(),
        "hermes_home": os.environ.get("HERMES_HOME", ""),
    }


def read_draft(draft_dir) -> dict:
    """Read a staged (not yet installed) skill so the widget can show it."""
    if not draft_dir:
        return {}
    directory = Path(draft_dir)
    skill_md = directory / "SKILL.md"
    if not skill_md.is_file():
        return {}
    files = sorted(
        str(p.relative_to(directory)).replace("\\", "/")
        for p in directory.rglob("*")
        if p.is_file()
    )
    parts = {}
    for name in ("SKILL.md", "glossary.md", "patterns.md", "cheatsheet.md"):
        candidate = directory / name
        if candidate.is_file():
            parts[name] = len(candidate.read_text(encoding="utf-8", errors="replace"))
    return {
        "dir": str(directory),
        "file_count": len(files),
        "files": files[:120],
        "chapters": sum(1 for f in files if f.startswith("chapters/")),
        "parts": parts,
        "skill_md": skill_md.read_text(encoding="utf-8", errors="replace")[:20000],
    }


def newest_draft_root() -> str:
    root = REPO / "staging"
    if not root.is_dir():
        return ""
    dirs = [d for d in root.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()]
    if not dirs:
        return ""
    return str(max(dirs, key=lambda d: d.stat().st_mtime))


def main() -> int:
    parser = argparse.ArgumentParser(description="book-to-skill (Hermes fork) -- review dashboard")
    parser.add_argument("url")
    parser.add_argument("--out", default=str(REPO / "b2s_fetched"))
    parser.add_argument("--force", default=None, choices=[None, "raw-md", "trafilatura", "bs4", "stdlib"])
    parser.add_argument("--html", default=str(REPO / "dashboard" / "render.html"))
    parser.add_argument("--draft-dir", default="", help="staged skill to show (default: newest under staging/)")
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
        "draft": read_draft(args.draft_dir or newest_draft_root()),
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
