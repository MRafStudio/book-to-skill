"""Generic strategies: raw markdown, and the HTML cleaning cascade.

The HTML cascade deliberately mirrors upstream's order in
``book_to_skill/parsers/html.py`` (trafilatura -> bs4 -> stdlib) so a URL fetched
here and a local HTML file fed to ``extract.py`` clean up the same way. Unlike
upstream, the winner is reported instead of only logged -- the dashboard needs to
show which cleaner answered.
"""
from __future__ import annotations

import re

from book_to_skill.plugins.base import (
    FetchResult,
    Plugin,
    decode,
    http_get,
    looks_like_path,
    page_title,
)

MD_SUFFIXES = (".md", ".markdown", ".mdx", ".mkd")
GITHUB_BLOB = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/(?:blob|raw)/(.+)$")


def _without_query(url: str) -> str:
    return url.split("#", 1)[0].split("?", 1)[0]


def clean_html(raw_html: str, force: str | None = None):
    """HTML -> text. Returns (text, winner, notes).

    force: None (auto cascade) | "trafilatura" | "bs4" | "stdlib".
    """
    notes: list[str] = []

    if force in (None, "trafilatura"):
        try:
            import trafilatura  # type: ignore

            extracted = None
            try:
                extracted = trafilatura.extract(
                    raw_html,
                    include_tables=True,
                    # include_formatting MUST stay True with output_format="markdown":
                    # with False the extractor silently drops every heading (measured on
                    # docs.python.org: 22 headings -> 0), which destroys the document
                    # structure the chunking/chapter detection depends on.
                    include_formatting=True,
                    output_format="markdown",
                )
            except Exception as exc:  # malformed HTML raising inside trafilatura
                notes.append(f"trafilatura error: {type(exc).__name__}")
            if extracted and extracted.strip():
                return extracted, "trafilatura", notes
            notes.append("trafilatura: no confident extraction")
        except ImportError:
            notes.append("trafilatura not installed")
        if force == "trafilatura":
            notes.append("forced trafilatura unavailable -- falling back")

    if force in (None, "bs4"):
        try:
            from bs4 import BeautifulSoup  # type: ignore

            soup = BeautifulSoup(raw_html, "html.parser")
            for element in soup(["script", "style", "head"]):
                element.decompose()
            return soup.get_text(separator="\n"), "beautifulsoup4", notes
        except ImportError:
            notes.append("beautifulsoup4 not installed")
        if force == "bs4":
            notes.append("forced bs4 unavailable -- falling back")

    # Upstream's stdlib parser, reused as-is: it strips script/style/head only,
    # so page chrome (nav, sidebars, footers) survives. Say so, loudly.
    from book_to_skill.parsers.html import _HTMLTextExtractor

    parser = _HTMLTextExtractor()
    parser.feed(raw_html)
    notes.append(
        "WARNING: stdlib parser keeps page chrome (nav/footer/sidebar) -- "
        "install trafilatura for boilerplate removal"
    )
    return parser.get_text(), "stdlib-html-parser", notes


class RawMarkdownPlugin(Plugin):
    """Direct markdown sources: no cleaning needed, nothing to strip."""

    NAME = "raw-markdown"
    PRIORITY = 90

    def matches(self, url: str) -> float:
        # A local path is never a network source: claiming it here is exactly how
        # "D:\...\HERMES.md" ended up in urlopen ("unknown url type: d"). The
        # local-file plugin owns disk paths.
        if looks_like_path(url):
            return 0.0
        if GITHUB_BLOB.match(url):
            return 1.0
        if _without_query(url).lower().endswith(MD_SUFFIXES):
            return 0.95
        return 0.0

    def run(self, url: str, force: str | None = None) -> FetchResult:
        target = url
        notes = ["source is already plain markdown"]
        m = GITHUB_BLOB.match(url)
        if m:
            user, repo, rest = m.groups()
            target = f"https://raw.githubusercontent.com/{user}/{repo}/{rest}"
            notes = [f"rewrote GitHub blob URL to {target}"]
        body, _final, headers = http_get(target)
        text = decode(body, headers)
        return FetchResult(
            url=url,
            ok=bool(text.strip()),
            strategy=self.NAME,
            content=text,
            kind="markdown",
            title=page_title(text),
            raw_bytes=len(body),
            notes=notes,
        )


class HtmlCascadePlugin(Plugin):
    """Any http(s) page that is not already markdown."""

    NAME = "html-cascade"
    PRIORITY = 50

    def matches(self, url: str) -> float:
        # Same rule as raw-markdown: a path on disk is local-file's business.
        if looks_like_path(url):
            return 0.0
        return 0.5 if url.lower().startswith(("http://", "https://")) else 0.0

    def run(self, url: str, force: str | None = None) -> FetchResult:
        body, final_url, headers = http_get(url)
        markup = decode(body, headers)
        text, winner, notes = clean_html(markup, force=force)
        notes.insert(0, f"{len(body)} bytes from {final_url}")
        return FetchResult(
            url=url,
            ok=bool(text.strip()),
            strategy=winner,
            content=text,
            kind="markdown" if winner == "trafilatura" else "text",
            title=page_title(markup),
            raw_bytes=len(body),
            notes=notes,
        )
