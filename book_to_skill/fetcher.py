"""Fetch a remote page into a local, boilerplate-free source file.

Hermes-fork addition. Nothing here changes upstream behaviour: the output is a
plain local ``.md`` / ``.txt`` that the unchanged ``scripts/extract.py`` pipeline
consumes. The URL -> text cascade itself lives in ``book_to_skill/plugins``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from book_to_skill.plugins import load_plugins

# Advisory only: their presence means a cleaner failed to strip page chrome.
JUNK_MARKERS = (
    "table of contents",
    "skip to content",
    "navigation",
    "previous topic",
    "next topic",
    "© copyright",
    "all rights reserved",
    "created using sphinx",
    "cookie",
    "privacy policy",
    "log in",
    "sign in",
    "subscribe",
    "advertisement",
    "share this",
    "back to top",
)


def slug_for_url(url: str) -> str:
    slug = re.sub(r"^https?://", "", url)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()
    return slug[:80] or "fetched"


def count_junk(text: str) -> dict:
    low = text.lower()
    return {marker: low.count(marker) for marker in JUNK_MARKERS if low.count(marker)}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class Fetcher:
    """Runs the plugin cascade for one URL and writes the local source file."""

    def __init__(self, force: str | None = None):
        self.force = force
        self.plugins = load_plugins()

    def fetch(self, url: str):
        """Return (FetchResult | None, attempts). Every attempt is recorded."""
        candidates = []
        for plugin in self.plugins:
            score = plugin.matches(url)
            if score <= 0:
                continue
            # A forced strategy narrows the field instead of overriding the run.
            # local-file rides along with both families: a local .md is "raw", a
            # local .html goes through the cleaning cascade (see plugin_local_file).
            if self.force == "raw-md" and plugin.NAME not in ("raw-markdown", "local-file"):
                continue
            if self.force in ("trafilatura", "bs4", "stdlib") and plugin.NAME not in (
                "html-cascade",
                "local-file",
            ):
                continue
            candidates.append((score, plugin))

        if not candidates:
            return None, [
                {"plugin": "-", "score": 0, "ok": False, "note": "no plugin matched this URL"}
            ]

        candidates.sort(key=lambda item: (-item[0], -item[1].PRIORITY))
        attempts = []
        chosen = None
        for score, plugin in candidates:
            try:
                result = plugin.run(url, force=self.force)
            except Exception as exc:
                attempts.append(
                    {
                        "plugin": plugin.NAME,
                        "score": score,
                        "ok": False,
                        "note": f"{type(exc).__name__}: {exc}",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            attempts.append(
                {
                    "plugin": result.strategy or plugin.NAME,
                    "score": score,
                    "ok": bool(result.ok),
                    "note": "; ".join(result.notes),
                    # The plugin's own wording beats a generic message: "файл не
                    # найден: D:\..." is actionable, "no strategy produced text"
                    # is what made the dashboard look broken ("болт").
                    "error": result.error or "",
                }
            )
            if result.ok:
                chosen = result
                break
        return chosen, attempts

    def fetch_to_dir(self, url: str, out_dir) -> dict:
        """Fetch *url*, write ``<slug>.md`` + a ``.report.json`` beside it."""
        result, attempts = self.fetch(url)
        report = {"url": url, "attempts": attempts, "force": self.force}
        if result is None:
            report["ok"] = False
            # Never swallow the reason: the first attempt is usually the most
            # specific one (local-file says "file not found" straight away).
            report["error"] = next(
                (a.get("error") for a in attempts if a.get("error")),
                next((a.get("note") for a in attempts if a.get("note")), ""),
            ) or "no strategy produced text"
            return report

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        name = slug_for_url(url) + (".md" if result.kind == "markdown" else ".txt")
        path = out_dir / name
        # The header is provenance for the generated skill: upstream's extractor
        # carries it into full_text.txt, so the source URL survives the pipeline.
        header = (
            f"SOURCE: {result.title or name}\n"
            f"URL: {url}\n"
            f"STRATEGY: {result.strategy}\n\n"
        )
        path.write_text(header + result.content, encoding="utf-8")

        junk = count_junk(result.content)
        report.update(
            {
                "ok": True,
                "title": result.title,
                "strategy": result.strategy,
                "kind": result.kind,
                "raw_bytes": result.raw_bytes,
                "chars": len(result.content),
                "words": len(result.content.split()),
                "est_tokens": estimate_tokens(result.content),
                "junk": junk,
                "junk_total": sum(junk.values()),
                "source_file": str(path),
                "notes": result.notes,
                "preview": result.content[:6000],
            }
        )
        (out_dir / (name + ".report.json")).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report
