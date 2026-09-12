"""Local sources: a path on disk is a document, not a URL.

Hermes-fork addition, kept in its own ``plugin_*.py`` so upstream files stay
untouched. Two jobs, one reason:

1. A path must never reach ``urlopen``. Before this plugin existed the cascade
   fed ``D:\\...\\HERMES.md`` to raw-markdown, which happily claimed any ``.md``
   and died with ``unknown url type: d`` — the dashboard showed a bare failure
   ("болт") for a file sitting on the very disk Hermes runs from.

2. Format detection is NOT reinvented here. Upstream already knows how: it reads
   the extension (``SUPPORTED_EXTENSIONS`` in ``config.py``), sniffs magic bytes
   when the extension is missing or unknown (``%PDF``, ``PK`` + ``mimetype`` /
   ``word/document.xml``), and dispatches to the right parser (text / html / pdf
   via docling|pdftotext|pypdf|pdfminer / epub / docx / rtf / calibre). So this
   plugin delegates to ``book_to_skill.utils.extract_single_file`` and only keeps
   a small reading fallback for a checkout where that package is unavailable.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from book_to_skill.plugins.base import (
    FetchResult,
    Plugin,
    looks_like_path,  # noqa: F401  (re-exported: both sides need the same rule)
)

MD_SUFFIXES = (".md", ".markdown", ".mdx", ".mkd")
TEXT_SUFFIXES = (".txt", ".text", ".rst", ".org", ".adoc", ".asciidoc")
HTML_SUFFIXES = (".html", ".htm", ".xhtml")
ENCODINGS = ("utf-8-sig", "utf-8", "cp1251", "cp866", "latin-1")


def read_text_any(path: Path) -> tuple[str, str]:
    """Read *path*, trying the encodings in order. Returns (text, encoding_used).

    Newlines are normalised to ``\\n`` on purpose: reading bytes keeps CRLF, while
    the rest of the pipeline (and every editor) works with LF, and a stray ``\\r``
    ends up glued to headings and chapter markers.
    """
    raw = path.read_bytes()
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
        enc = "utf-8/replace"
    return text.replace("\r\n", "\n").replace("\r", "\n"), enc


def pdf_to_text(path: Path) -> str:
    """pdftotext if poppler is present; empty string tells the caller to say so."""
    exe = "pdftotext"
    with tempfile.TemporaryDirectory(prefix="b2s-pdf-") as tmp:
        out = Path(tmp) / "out.txt"
        try:
            proc = subprocess.run(
                [exe, "-layout", str(path), str(out)],
                capture_output=True,
                timeout=180,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if proc.returncode != 0 or not out.is_file():
            return ""
        return out.read_text(encoding="utf-8", errors="replace")


def heading_title(text: str, fallback: str) -> str:
    """First markdown heading wins; otherwise the file name."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            candidate = stripped.lstrip("#").strip()
            if candidate:
                return candidate
        elif stripped:
            break
    return fallback


class LocalFilePlugin(Plugin):
    """Reads sources that live on disk. Never touches the network."""

    NAME = "local-file"
    PRIORITY = 200

    def matches(self, src: str) -> float:
        if not looks_like_path(src):
            return 0.0
        path = Path(src).expanduser()
        if path.exists():
            return 1.0
        # A path that is not there is still OUR business: returning 0.0 here would
        # push it back to the network plugins and reproduce "unknown url type".
        # 0.9 keeps us first (network plugins now refuse paths anyway) and lets the
        # report say "файл не найден" in plain words.
        return 0.9

    def run(self, src: str, force: str | None = None) -> FetchResult:
        path = Path(src).expanduser()
        if not path.exists():
            return FetchResult(
                url=src,
                ok=False,
                strategy=self.NAME,
                error=f"файл не найден: {path}",
                notes=[f"локальный путь не существует: {path}"],
            )
        if path.is_dir():
            return self._run_dir(src, path)
        if self._needs_own_decoder(path):
            return self._run_fallback(src, path)
        native = self._run_native(src, path, force)
        if native is not None:
            return native
        return self._run_fallback(src, path)

    def _needs_own_decoder(self, path: Path) -> bool:
        """True when upstream's text reader would mojibake this file.

        Upstream tries utf-8 -> cp1252 -> latin-1, and both fallbacks accept ANY
        byte sequence: nothing raises, so a Russian document saved as cp1251 comes
        back as garbage instead of an error. Those files are read here, where the
        chain actually includes cp1251/cp866.
        """
        if path.suffix.lower() not in (MD_SUFFIXES + TEXT_SUFFIXES):
            return False
        try:
            head = path.read_bytes()[:262144]
        except OSError:
            return False
        # A BOM means the question is already answered — let upstream read it.
        for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
            if head.startswith(bom):
                return False
        try:
            head.decode("utf-8")
        except UnicodeDecodeError:
            return True
        return False

    # ── upstream extractor: format detection lives there, we just call it ──────

    def _run_native(self, src: str, path: Path, force: str | None) -> FetchResult | None:
        """Delegate to ``extract_single_file``; ``None`` means "not available"."""
        try:
            from book_to_skill.utils import extract_single_file
        except Exception:
            return None
        # The pane's engine switch (technical | text) decides PDF strategy. It
        # travels via env because the cascade contract is run(src, force) and
        # every other plugin is network-only.
        mode = (os.environ.get("B2S_EXTRACTION_MODE") or "technical").strip().lower()
        if force in ("text", "technical"):
            mode = force
        try:
            # Upstream is chatty on stdout; keep the API response clean.
            with contextlib.redirect_stdout(io.StringIO()):
                info = extract_single_file(path, mode, "no")
        except Exception as exc:
            name = type(exc).__name__
            return FetchResult(
                url=src,
                ok=False,
                strategy=self.NAME,
                error=f"{name}: {exc}",
                notes=[f"родной экстрактор отказал на {path.name} ({name})"],
            )
        text = (info.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            return FetchResult(
                url=src,
                ok=False,
                strategy=self.NAME,
                error="экстрактор вернул пустой текст",
                notes=[f"формат: {info.get('format')}, метод: {info.get('extraction_method')}"],
            )
        notes = [
            f"формат определён как {info.get('format')} (расширение/магия файла)",
            f"метод извлечения: {info.get('extraction_method')}",
            f"{info.get('chars')} символов, {info.get('words')} слов",
        ]
        pages = info.get("pages")
        if pages:
            notes.append(f"{info.get('pages_label')}: {pages}")
        if info.get("images_dropped"):
            notes.append(f"картинок не извлечено: {info.get('images_dropped')}")
        return FetchResult(
            url=src,
            ok=True,
            strategy=f"upstream-{info.get('extraction_method') or 'extractor'}",
            content=text,
            kind="markdown" if path.suffix.lower() in MD_SUFFIXES else "text",
            title=info.get("filename") or heading_title(text, path.name),
            raw_bytes=path.stat().st_size,
            notes=notes,
        )

    # ── fallback: same reader the plugin always had, for a bare checkout ───────

    def _run_fallback(self, src: str, path: Path) -> FetchResult:
        suffix = path.suffix.lower()
        if suffix in HTML_SUFFIXES:
            from book_to_skill.plugins.generic import clean_html

            raw = read_text_any(path)[0]
            text, winner = clean_html(raw)
            if not text.strip():
                return FetchResult(
                    url=src, ok=False, strategy=self.NAME,
                    error="не удалось выделить текст из локального HTML",
                )
            return FetchResult(
                url=src, ok=True, strategy=f"local-file/{winner}", content=text,
                kind="markdown", title=heading_title(text, path.name),
                raw_bytes=path.stat().st_size,
                notes=[f"локальный HTML очищен стратегией {winner}"],
            )
        if suffix == ".pdf":
            text = pdf_to_text(path)
            if not text.strip():
                return FetchResult(
                    url=src, ok=False, strategy=self.NAME,
                    error="pdftotext не дал текста (poppler не установлен или PDF без текстового слоя)",
                    notes=["установи poppler (pdftotext) либо сконвертируй PDF в текст вручную"],
                )
            return FetchResult(
                url=src, ok=True, strategy="local-file/pdftotext", content=text,
                kind="text", title=path.name, raw_bytes=path.stat().st_size,
                notes=["локальный PDF прочитан через pdftotext"],
            )
        text, enc = read_text_any(path)
        if not text.strip():
            return FetchResult(
                url=src, ok=False, strategy=self.NAME,
                error=f"файл пуст: {path}",
            )
        is_md = suffix in MD_SUFFIXES
        return FetchResult(
            url=src,
            ok=True,
            strategy=self.NAME,
            content=text,
            kind="markdown" if is_md else "text",
            title=heading_title(text, path.name) if is_md else path.name,
            raw_bytes=path.stat().st_size,
            notes=[f"прочитан локальный файл, кодировка {enc} (родной экстрактор недоступен)"],
        )

    # ── directory: one source out of several documents ─────────────────────────

    def _run_dir(self, src: str, path: Path) -> FetchResult:
        wanted = MD_SUFFIXES + TEXT_SUFFIXES + HTML_SUFFIXES
        files = sorted(
            (p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in wanted),
            key=lambda p: str(p).lower(),
        )
        if not files:
            return FetchResult(
                url=src,
                ok=False,
                strategy=self.NAME,
                error=f"в каталоге нет .md/.txt: {path}",
                notes=["положи в каталог markdown или текстовые файлы"],
            )
        chunks, titles = [], []
        for item in files:
            rel = item.relative_to(path).as_posix()
            title = item.name
            if item.suffix.lower() in HTML_SUFFIXES:
                from book_to_skill.plugins.generic import clean_html

                body = clean_html(read_text_any(item)[0])[0]
            else:
                body, _ = read_text_any(item)
            chunks.append(f"# {rel}\n\n{body.strip()}\n")
            titles.append(title)
        content = "\n\n".join(chunks)
        return FetchResult(
            url=src,
            ok=True,
            strategy=f"{self.NAME}/dir",
            content=content,
            kind="markdown",
            title=f"{path.name} ({len(files)} файлов)",
            notes=[f"каталог собран в один источник: {len(files)} файлов по алфавиту"],
        )
