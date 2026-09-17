#!/usr/bin/env python3
"""Страницы документации против скиллов категории - раздел «для добора» аудита.

Портал документации r_keeper - это Confluence: дерево раздела обходится по REST
(``/rest/api/content/<id>/child/page``) от корневой страницы, авторизация не нужна.
Список страниц кэшируется на диск: раздел отчёта собирается и тогда, когда портала
нет - в отчёте при этом стоит дата снимка, а не пустое место.

Со скиллами страницы сверяются по ЧИСЛОВОМУ id из ``source.url`` в
``metadata.json``, а не по имени файла-слага: у части скиллов в метаданных записан
адрес ВЕРСИИ страницы (id на единицу меньше самой страницы), поэтому сверка «по
имени» даёт ложные «не закрыто». Отсюда допуск ±1 при сравнении id.

Модуль ничего не пишет в скиллы: только читает профиль и (при ``online``) кэш.
"""

from __future__ import annotations

import json
import pathlib
import re
import time
import urllib.error  # noqa: F401  - держим импорт: сюда прилетает сбой портала
import urllib.request

BASE = "https://docs.rkeeper.ru"
CHILD_LIMIT = 100
MAX_DEPTH = 4
CACHE_DIRNAME = ".cache"
# id страницы - длинное число в адресе (``pageId=19605640``, ``...-19605640.html``),
# короткие числа в адресе могут быть чем угодно другим.
ID_RE = re.compile(r"(\d{6,})")
ID_TOLERANCE = 1


def _api(path: str, timeout: float = 20.0) -> dict:
    """GET к REST портала. Ошибку не глотаем - её обрабатывает ``scan``."""
    req = urllib.request.Request(
        BASE + path,
        headers={"User-Agent": "b2s-audit/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - адрес свой
        return json.loads(resp.read().decode("utf-8"))


def children_of(page_id: str) -> list[dict]:
    """Прямые дочерние страницы (постранично: у Confluence предел выборки)."""
    out: list[dict] = []
    start = 0
    while True:
        data = _api(f"/rest/api/content/{page_id}/child/page?limit={CHILD_LIMIT}&start={start}")
        results = data.get("results") or []
        for page in results:
            links = page.get("_links") or {}
            out.append({
                "id": str(page.get("id") or ""),
                "title": (page.get("title") or "").strip(),
                "webui": links.get("webui") or "",
            })
        if len(results) < CHILD_LIMIT:
            return out
        start += CHILD_LIMIT


def fetch_tree(root_id: str) -> list[dict]:
    """Дерево раздела в ширину: сама корневая страница в список не входит.

    Глубина ограничена (``MAX_DEPTH``): защита от петли, если портал отдаст
    страницу своим же потомком.
    """
    tree: list[dict] = []
    seen: set[str] = set()
    queue: list[tuple[str, int]] = [(str(root_id), 1)]
    while queue:
        pid, depth = queue.pop(0)
        if pid in seen or depth > MAX_DEPTH:
            continue
        seen.add(pid)
        for child in children_of(pid):
            if not child["id"] or child["id"] in seen:
                continue
            child["depth"] = depth
            child["parent"] = pid
            tree.append(child)
            queue.append((child["id"], depth + 1))
    return tree


def cache_file(cache_dir, root_id: str) -> pathlib.Path:
    return pathlib.Path(cache_dir) / f"docs-pages-{root_id}.json"


def load_cache(cache_dir, root_id: str) -> dict | None:
    """Снимок прошлых прогонов. Битая или чужая версия файла = «кэша нет»."""
    path = cache_file(cache_dir, root_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or str(data.get("root_id")) != str(root_id):
        return None
    return data


def save_cache(cache_dir, root_id: str, tree: list[dict]) -> None:
    """Кэш только для чтения агентом: в git не едет (см. ``.cache`` в ``.gitignore``)."""
    path = cache_file(cache_dir, root_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "root_id": str(root_id),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M"),
        "tree": tree,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                    encoding="utf-8", newline="\n")


def skill_page_ids(root: pathlib.Path) -> dict[str, str]:
    """Адреса страниц, на которых основаны скиллы категории: ``{id: имя скилла}``.

    Читаются только ``metadata.json`` своего каталога; скилл без метаданных в
    сверке не участвует - это не ошибка, а его состояние.
    """
    out: dict[str, str] = {}
    root = pathlib.Path(root)
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if not d.is_dir():
            continue
        meta = d / "metadata.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        src = data.get("source") if isinstance(data, dict) else None
        url = ""
        if isinstance(src, dict):
            url = str(src.get("url") or "")
        elif isinstance(src, str):
            url = src
        for m in ID_RE.findall(url):
            out.setdefault(m, d.name)
    return out


def _skill_for(page_id: int, ids: dict[str, str]) -> str | None:
    """Скилл, закрывающий страницу: сравнение по id с допуском ±1 (см. шапку)."""
    for key, skill in ids.items():
        if abs(int(key) - page_id) <= ID_TOLERANCE:
            return skill
    return None


def compare(tree: list[dict], ids: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """``(закрытые, недобранные)`` - по каждой странице дерева.

    В записи остаётся имя скилла и признак контейнера: у контейнера есть дети, и
    скилл по нему обычно не делают (это оглавление, а не материал).
    """
    parents = {p.get("parent") for p in tree}
    covered: list[dict] = []
    missing: list[dict] = []
    for page in tree:
        try:
            pid = int(page["id"])
        except (KeyError, TypeError, ValueError):
            continue
        rec = {
            "id": str(page.get("id") or ""),
            "title": page.get("title") or "",
            "webui": page.get("webui") or "",
            "depth": page.get("depth") or 0,
            "container": str(page.get("id")) in parents,
        }
        skill = _skill_for(pid, ids)
        if skill:
            covered.append({**rec, "skill": skill})
        else:
            missing.append(rec)
    return covered, missing


def scan(root: pathlib.Path, root_id: str, cache_dir, online: bool = True) -> dict:
    """Раздел «страницы документации» для отчёта.

    ``online=True`` тянет дерево с портала и обновляет кэш; если портал молчит,
    берётся кэш и источник помечается ``cache``. ``online=False`` в сеть не ходит
    вообще - так работает сторож и офлайн-прогон.
    """
    tree: list[dict] = []
    source = "none"
    fetched_at = ""
    if online:
        try:
            tree = fetch_tree(root_id)
            fetched_at = time.strftime("%Y-%m-%d %H:%M")
            save_cache(cache_dir, root_id, tree)
            source = "rest"
        except Exception:  # noqa: BLE001 - портал не повод валить отчёт
            cached = load_cache(cache_dir, root_id)
            if cached:
                tree = cached.get("tree") or []
                fetched_at = str(cached.get("fetched_at") or "")
                source = "cache"
    else:
        cached = load_cache(cache_dir, root_id)
        if cached:
            tree = cached.get("tree") or []
            fetched_at = str(cached.get("fetched_at") or "")
            source = "cache"
    covered, missing = compare(tree, skill_page_ids(root))
    return {
        "ok": True,
        "source": source,
        "fetched_at": fetched_at,
        "root_id": str(root_id),
        "url_base": BASE,
        "pages": len(tree),
        "covered": covered,
        "missing": missing,
    }
