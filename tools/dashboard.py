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
import sqlite3
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


def hermes_home() -> Path:
    home = os.environ.get("HERMES_HOME")
    return Path(home) if home else Path.home() / ".hermes"


def discover_categories() -> list:
    """Categories = subdirectories of the active Hermes profile's skills root."""
    root = hermes_home() / "skills"
    if root.is_dir():
        names = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
        if names:
            return names
    return [DEFAULT_CATEGORY]


def session_info(override: str = "") -> dict:
    """The model the *current* session is actually running on.

    Read from the host's ``state.db`` (read-only): the most recently active
    session's dominant model, plus its measured token usage. The configured
    default in ``config.yaml`` is reported separately -- a running session can
    (and here does) differ from it.
    """
    info = {"source": "", "model": "", "provider": "", "session_id": "",
            "input_tokens": 0, "output_tokens": 0, "api_calls": 0, "est_cost_usd": 0.0,
            "started_at": "", "messages": 0, "config_default": ""}
    if override:
        info["model"], info["source"] = override, "передан агентом"
        return info

    db = hermes_home() / "state.db"
    if db.is_file():
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=15)
            cur = con.cursor()
            row = cur.execute(
                "select session_id from session_model_usage order by last_seen desc limit 1"
            ).fetchone()
            if row:
                sid = row[0]
                info["session_id"] = sid
                agg = cur.execute(
                    "select model, billing_provider, sum(api_call_count), sum(input_tokens), "
                    "sum(output_tokens), sum(coalesce(actual_cost_usd, 0)), "
                    "sum(coalesce(estimated_cost_usd, 0)) "
                    "from session_model_usage where session_id = ? "
                    "group by model, billing_provider order by sum(api_call_count) desc limit 1",
                    (sid,),
                ).fetchone()
                if agg:
                    info["model"] = agg[0] or ""
                    info["provider"] = agg[1] or ""
                    info["api_calls"] = agg[2] or 0
                    info["input_tokens"] = agg[3] or 0
                    info["output_tokens"] = agg[4] or 0
                    actual, estimated = agg[5] or 0.0, agg[6] or 0.0
                    info["est_cost_usd"] = round(actual if actual > 0 else estimated, 4)
                    info["cost_kind"] = "факт" if actual > 0 else "оценка"
                srow = cur.execute(
                    "select started_at, message_count, model from sessions where id = ?", (sid,)
                ).fetchone()
                if srow:
                    info["started_at"] = str(srow[0] or "")
                    info["messages"] = srow[1] or 0
                    if not info["model"]:
                        info["model"] = srow[2] or ""
                info["source"] = "state.db (живая сессия)"
            con.close()
        except (sqlite3.Error, OSError) as exc:
            info["source"] = f"state.db недоступен: {type(exc).__name__}"
    else:
        info["source"] = "state.db не найден"

    cfg = hermes_home() / "config.yaml"
    if cfg.is_file():
        try:
            text = cfg.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        default = provider = ""
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if re.match(r"^\w", line):
                if default or provider:
                    break
                continue
            key, _, value = line.strip().partition(":")
            value = value.strip().strip("'\"")
            if key == "default" and not default:
                default = value
            elif key == "provider" and not provider:
                provider = value
        info["config_default"] = f"{default} · {provider}" if default else provider
    return info


def engine_info(session: dict) -> dict:
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
        "session": session,
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


def suggest_skill_name(url: str, title: str = "", source_file: str = "") -> str:
    """Human-meaningful short skill name: ``<site>-<topic>``.

    The source *slug* (``docs-python-org-3-library-pathlib-html``) is a machine
    label: it names the fetched file and the staging dir, but as a skill name it
    is useless -- three tokens of noise before the topic, and Hermes' trigger
    line has to match on it. So build the default from the two things a human
    recognises: the site and the topic.

    ``https://docs.python.org/3/library/pathlib.html`` + title
    ``pathlib - Object-oriented filesystem paths`` -> ``python-pathlib``.

    The widget shows this pre-filled and editable; an empty value means the
    agent picks the name itself.
    """
    def slugify(text: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "-", text or "").strip("-").lower()

    # Topic: the last meaningful URL segment wins -- it is the *topic* of the
    # page ("/3/library/pathlib.html" -> "pathlib", "/reference/react/useEffect"
    # -> "useeffect"), while the <title> is prose ("What's new in C# 13") and
    # slugifies into noise. Title and source file are fallbacks.
    generic = {"index", "readme", "home", "default", "overview", "introduction", "intro"}
    topic = ""
    m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://([^/?#]+)([^?#]*)", url or "")
    if m:
        segments = [s for s in m.group(2).strip("/").split("/") if s]
        while segments:
            last = re.sub(r"\.(html?|php|aspx?|md|txt|rst)$", "", segments[-1], flags=re.I)
            if slugify(last) not in generic and not last.isdigit():
                topic = slugify(last)
                break
            segments.pop()
    if not topic and title:
        topic = slugify(re.split(r"[—\-–:|(]", title, maxsplit=1)[0])
    if not topic and source_file:
        topic = slugify(Path(source_file).stem)

    # Site: registrable-ish label of the host ("docs.python.org" -> "python").
    site = ""
    host = re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://([^/?#]+)", url or "")
    if host:
        labels = [p for p in host.group(1).split(":")[0].split(".") if p]
        drop = {"www", "docs", "doc", "com", "org", "net", "io", "dev", "ru", "en", "3"}
        keep = [p for p in labels if p.lower() not in drop]
        site = slugify(keep[-1] if keep else (labels[0] if labels else ""))

    name = "-".join(p for p in (site, topic) if p)
    return name[:48].strip("-") or slugify(Path(source_file).stem)[:48]


def main() -> int:
    parser = argparse.ArgumentParser(description="book-to-skill (Hermes fork) -- review dashboard")
    parser.add_argument("url")
    parser.add_argument("--out", default=str(REPO / "b2s_fetched"))
    parser.add_argument("--force", default=None, choices=[None, "raw-md", "trafilatura", "bs4", "stdlib"])
    parser.add_argument("--html", default=str(REPO / "dashboard" / "render.html"))
    parser.add_argument("--draft-dir", default="", help="staged skill to show (default: newest under staging/)")
    parser.add_argument("--model", default="", help="override the session model label")
    args = parser.parse_args()

    fetcher = Fetcher(force=args.force)
    report = fetcher.fetch_to_dir(args.url, args.out)
    session = session_info(args.model)

    data = {
        "report": report,
        "plugins": [
            {"name": p.NAME, "priority": p.PRIORITY, "score": p.matches(args.url)}
            for p in fetcher.plugins
        ],
        "categories": discover_categories(),
        "engine": engine_info(session),
        "default_category": DEFAULT_CATEGORY,
        "suggested_name": suggest_skill_name(
            report.get("url", ""), report.get("title", ""), report.get("source_file", "")
        ),
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
