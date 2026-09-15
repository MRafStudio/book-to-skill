#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Оглавление Confluence и поиск страниц без скиллов.

Вытаскивает всё дерево страниц пространства через REST, сопоставляет source_url
скиллов и печатает непокрытые страницы с объёмом текста и рабочей ссылкой.

Запуск:
    python fetch_toc.py --space RK7 --skills-dir <каталог скиллов>
    python fetch_toc.py --space RK7 --skills-dir <каталог> --root-page 19605640
    python fetch_toc.py --space RK7 --skills-dir <каталог> --search RK7Query
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import ssl
import sys
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def make_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def api(url: str, ctx: ssl.SSLContext) -> dict:
    req = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=60, context=ctx).read().decode("utf-8", "replace"))


def all_pages(base: str, space: str, ctx: ssl.SSLContext) -> list[dict]:
    out, start = [], 0
    while True:
        u = (f"{base}/rest/api/content?spaceKey={space}&type=page&limit=100&start={start}"
             "&expand=ancestors,version")
        d = api(u, ctx)
        batch = d.get("results", [])
        out.extend(batch)
        if len(batch) < 100:
            return out
        start += 100


def text_len(base: str, pid: str, ctx: ssl.SSLContext) -> tuple[int, str]:
    u = f"{base}/rest/api/content/{pid}?expand=body.storage,version"
    d = api(u, ctx)
    body = ((d.get("body") or {}).get("storage") or {}).get("value", "")
    plain = re.sub(r"<[^>]+>", " ", body)
    plain = re.sub(r"\s+", " ", plain).strip()
    return len(plain), (((d.get("version") or {}).get("when") or "")[:10])


def readable_url(base: str, pid: str, ctx: ssl.SSLContext) -> str:
    """Читаемый URL получаем редиректом - транслит не угадываем."""
    try:
        req = urllib.request.Request(f"{base}/pages/viewpage.action?pageId={pid}", headers={"User-Agent": "Mozilla/5.0"})
        return urllib.request.urlopen(req, timeout=30, context=ctx).geturl()
    except Exception as exc:  # noqa: BLE001
        return f"<ошибка: {exc}>"


def skill_sources(skills_dir: pathlib.Path) -> dict[str, str]:
    """id источника -> имя скилла. Источник берём из metadata.json (source.url),
    при его отсутствии - из source_url в шапке SKILL.md."""
    found: dict[str, str] = {}
    for d in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
        url = ""
        meta = d / "metadata.json"
        if meta.is_file():
            try:
                data = json.loads(meta.read_text(encoding="utf-8", errors="replace"))
                src = data.get("source") or {}
                url = src.get("url") or data.get("source_url") or ""
            except Exception:  # noqa: BLE001
                url = ""
        if not url:
            f = d / "SKILL.md"
            if f.is_file():
                m = re.search(r'source_url:\s*"?([^\s"]+)"?', f.read_text(encoding="utf-8", errors="replace"))
                url = m.group(1) if m else ""
        if url:
            mid = re.search(r"-(\d+)\.html", url)
            if mid:
                found[mid.group(1)] = d.name
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://docs.rkeeper.ru", help="корень сайта")
    ap.add_argument("--space", default="RK7", help="ключ пространства Confluence")
    ap.add_argument("--skills-dir", required=True, help="каталог скиллов")
    ap.add_argument("--root-page", default=None, help="id корня поддерева (ограничить вывод)")
    ap.add_argument("--search", default=None, help="CQL-поиск по содержимому вместо дерева")
    ap.add_argument("--empty-ok", action="store_true", help="показывать пустые страницы (контейнеры)")
    args = ap.parse_args()

    ctx = make_ctx()
    base = args.base.rstrip("/")
    sd = pathlib.Path(args.skills_dir)
    covered = skill_sources(sd)
    print(f"скиллов с source_url: {len(covered)}")

    if args.search:
        cql = f'space={args.space} and siteSearch ~ "{args.search}"'
        pages = []
        start = 0
        while True:
            u = f"{base}/rest/api/content/search?cql={urllib.parse.quote(cql)}&limit=100&start={start}&expand=ancestors"
            d = api(u, ctx)
            b = d.get("results", [])
            pages.extend(b)
            if len(b) < 100:
                break
            start += 100
        print(f"страниц по запросу '{args.search}': {len(pages)}\n")
        for p in pages:
            own = "ЕСТЬ скилл: " + covered[p["id"]] if p["id"] in covered else "скилла нет"
            anc = " > ".join(a["title"] for a in (p.get("ancestors") or []))
            print(f"  {p['title'][:60]:62s} [{own}]\n      {anc}")
        return 0

    pages = all_pages(base, args.space, ctx)
    print(f"страниц в пространстве {args.space}: {len(pages)}")

    if args.root_page:
        sub = [p for p in pages
               if p["id"] == args.root_page
               or any(str(a["id"]) == args.root_page for a in (p.get("ancestors") or []))]
        print(f"из них в поддереве {args.root_page}: {len(sub)}")
    else:
        sub = pages

    missing = [p for p in sub if p["id"] not in covered]
    print(f"без скиллов: {len(missing)}\n")
    for p in sorted(missing, key=lambda x: x["title"]):
        n, when = text_len(base, p["id"], ctx)
        if n == 0 and not args.empty_ok:
            continue
        parent = next((a["title"] for a in reversed(p.get("ancestors") or [])), "-")
        print(f"  {p['title'][:58]:60s} {n:>6} симв | обн. {when or '-'} | родитель: {parent[:30]}")
        print(f"      {readable_url(base, p['id'], ctx)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
