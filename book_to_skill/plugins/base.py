"""Plugin contract for the URL -> local source cascade (Hermes fork addition).

A plugin is a small, self-contained processor that knows one way of turning a
remote URL into clean local text. Plugins are tried by descending match score
and every attempt is recorded, so the dashboard can explain *why* a strategy
won. Site-specific processors are dropped in as new ``plugin_*.py`` files next
to this one and are picked up automatically -- no edits to this registry and no
edits to upstream code.
"""
from __future__ import annotations

import urllib.request
from dataclasses import dataclass, field

# Some sites serve a stub (or a 403) to non-browser agents; a plain UA keeps the
# generic path usable without pulling in a browser stack.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)


@dataclass
class FetchResult:
    """Outcome of one strategy attempt."""

    url: str
    ok: bool
    strategy: str = ""
    content: str = ""
    kind: str = "text"  # "markdown" (headings preserved) | "text"
    title: str = ""
    raw_bytes: int = 0
    notes: list = field(default_factory=list)
    error: str = ""


class Plugin:
    """Base class. Subclass, set NAME/PRIORITY, implement matches()/run()."""

    NAME = "unnamed"
    PRIORITY = 0

    def matches(self, url: str) -> float:
        """Return 0.0..1.0 -- how strongly this plugin claims *url*.

        0.0 means "not mine". Scores order the cascade; site-specific plugins
        should return 1.0 for their own domain so they win over the generics.
        """
        return 0.0

    def run(self, url: str, force: str | None = None) -> FetchResult:
        raise NotImplementedError


def http_get(url: str, timeout: int = 60):
    """Plain GET. Returns (body_bytes, final_url, headers_dict)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(), resp.geturl(), dict(resp.headers)


def decode(body: bytes, headers: dict) -> str:
    """Decode a response body using the declared charset, falling back to UTF-8."""
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "").lower()
    enc = "utf-8"
    if "charset=" in ctype:
        enc = ctype.split("charset=", 1)[1].split(";")[0].strip().strip('"') or "utf-8"
    try:
        return body.decode(enc, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def page_title(markup: str) -> str:
    """Best-effort <title> extraction, used for the report and the file header."""
    import html as _html
    import re

    m = re.search(r"<title[^>]*>(.*?)</title>", markup, re.I | re.S)
    if not m:
        return ""
    return _html.unescape(m.group(1)).strip()[:200]
