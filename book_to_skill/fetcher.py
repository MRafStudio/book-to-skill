"""Fetch a remote page into a local, boilerplate-free source file.

Hermes-fork addition. Nothing here changes upstream behaviour: the output is a
plain local ``.md`` / ``.txt`` that the unchanged ``scripts/extract.py`` pipeline
consumes. The URL -> text cascade itself lives in ``book_to_skill/plugins``.
"""
from __future__ import annotations

import hashlib
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


SLUG_MAX = 80
# Хэш ПОЛНОГО адреса добавляется к слагу ВСЕГДА - и к коротким тоже. Иначе адреса, у
# которых различие не в хвосте, схлопываются в один ключ: `http://a/b` и `https://a/b`
# (схема отрезается), `a/b` и `a/B` (регистр теряется), `page?id=1` и `page?id=2`.
SLUG_HASH_LEN = 11         # '-' + 10 знаков sha1


def _legacy_owner(d: Path, legacy: str) -> str:
    """Кому УЖЕ выдан этот ключ: URL владельца, '?' если не понять, '' если ключ свободен.

    Ключ возвращаем только законному хозяину: файл сырья хранит в шапке строку
    ``URL: <адрес>``, а каталог черновика - источник в ``metadata.json``. Без этой сверки
    ЛЮБОЙ адрес с тем же началом получал бы чужой ключ - и два разных источника снова
    схлопывались бы в один файл.
    """
    for ext in (".md", ".txt"):
        f = d / (legacy + ext)
        if f.is_file():
            head = f.read_bytes()[:600].decode("utf-8", "replace")
            m = re.search(r"(?m)^URL:\s*(\S+)", head)
            return m.group(1).strip() if m else "?"
    meta = d / legacy / "metadata.json"
    if meta.is_file():
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            return "?"
        src = str(((data.get("source") or {}) if isinstance(data, dict) else {}).get("url") or "")
        return src.strip() or "?"
    return ""


def slug_for_url(url: str, keep_dirs=()) -> str:
    """Ключ источника: читаемый слаг + хэш ПОЛНОГО адреса.

    Ключ - это имя файла в ``b2s_fetched`` и имя каталога черновика в ``staging``. Он обязан
    различать ЛЮБЫЕ два адреса и при этом оставаться человекочитаемым: по нему панель
    показывает путь, а в ``metadata.json`` записан файл сырья.

    Устройство: ``<читаемая часть до 69 знаков>-<sha1(url)[:10]>``. Хэш берётся от ПОЛНОГО
    адреса (вместе со схемой, регистром и query), поэтому «сойтись» два разных адреса не
    могут, а вся длина остаётся предсказуемой - резать нечего и двойников не бывает.
    Было иначе: слаг просто обрезался до 80 знаков, и адреса с общим началом и разницей в
    хвосте (у страниц r_keeper там номер) давали один файл - владелец: «каким макаром новый
    url сходится с файлом анализа старого url?».

    ``keep_dirs`` - каталоги, где ключ уже РАБОТАЕТ (файлы сырья, staging). Старый ключ
    возвращается, только если он принадлежит ЭТОМУ адресу: ключ, однажды выданный, не
    меняется, иначе готовый черновик стал бы для панели невидимым.
    """
    slug = re.sub(r"^https?://", "", url)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()
    digest = hashlib.sha1(url.strip().encode("utf-8", "replace")).hexdigest()[:SLUG_HASH_LEN - 1]
    cut = slug[:SLUG_MAX - SLUG_HASH_LEN].rstrip("-")
    fresh = (cut + "-" + digest) if cut else (digest or "fetched")
    want = url.strip()
    for d in keep_dirs or ():
        old = _legacy_owner(Path(d), slug[:SLUG_MAX])
        if old and old != "?" and old == want:
            return slug[:SLUG_MAX]
    return fresh


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
        name = slug_for_url(url, keep_dirs=(out_dir,)) + (".md" if result.kind == "markdown" else ".txt")
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
