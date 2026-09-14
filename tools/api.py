#!/usr/bin/env python3
"""Детерминированное ядро book-to-skill — вызов БЕЗ участия LLM.

Зачем
-----
Загрузка источника, очистка текста, отчёт и перенос готового черновика в
``skills/<категория>/<имя>/`` — это чистая механика: тот же Python даёт тот же
результат. Гонять её через чат (LLM) бессмысленно: медленнее, дороже и
превращает кнопку панели в «событие в чате».

Поэтому механика вынесена в один модуль, который дёргают:
  * gateway-плагин Hermes (``plugins/b2s/dashboard/plugin_api.py``) — панель
    зовёт его через ``ctx.rest('/api/plugins/b2s/rerun', …)``;
  * локальный сервер ``tools/serve.py`` (ветка плана Б) — из страницы по HTTP;
  * терминал: ``python tools/api.py rerun --src … --strat auto``.

LLM остаётся там, где без него никак: написать главы черновика (проза) и
разобрать их. Это по-прежнему делается в чате.

Использование
-------------
    python tools/api.py health
    python tools/api.py categories          # из чего панель разрешает выбрать категорию
    python tools/api.py skills              # что уже стоит: имя = тема, страницы = главы
    python tools/api.py rerun  --src https://… --strat auto
    python tools/api.py state
    python tools/api.py install --name python-pathlib --cat software-development
    python tools/api.py install --name python-pathlib --confirm   # реальный перенос
    python tools/api.py install --name python-pathlib --confirm --mode append   # долив
    python tools/api.py install --name python-pathlib --confirm --mode replace  # с бэкапом
    python tools/api.py plan --name python-pathlib     # долив: что слить, что переписать
    python tools/api.py desc --cat software-development --text "…"   # описание категории
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import dashboard as dash  # noqa: E402
from serve import (  # noqa: E402  — логику прогона переиспользуем, не копируем
    explain_failure,
    load_state,
    run_fetch,
    save_state,
    summary_line,
)
# Слаг источника = ключ черновика в staging. Ядро уже зовёт его, когда называет
# файл очищенного текста (`b2s_fetched/<слаг>.md`) — берём ту же функцию, чтобы
# ключ каталога и имя файла не разъехались.
from book_to_skill.fetcher import slug_for_url  # noqa: E402

# Машинно-специфичных путей здесь нет: профиль ищет hermes_paths.detect_hermes_home
# по HERMES_HOME и типовым местам установки — иначе плагин не перенести на другую
# машину. Здесь определяем только рабочие каталоги самого клона.
STAGING = REPO / "staging"
FETCH_DIR = REPO / "b2s_fetched"   # сюда каскад кладёт очищенный текст и report.json
BACKUP_DIR = REPO / "backups"      # копия скилла перед правкой на месте (долив/замена)
BACKUP_KEEP = 5                    # сколько копий на один скилл держим
# Интерпретатор для гейтов (validate/scan). Плагин задаёт его через B2S_PYTHON:
# сам dashboard запущен как hermes.exe, и sys.executable в службе — не python.
SKILL_PY = Path(os.environ.get("B2S_PYTHON") or sys.executable)


# ── помощники ────────────────────────────────────────────────────────────────
# Профиль Hermes и интерпретатор гейтов ищет общий модуль: тот же код нужен
# установщику плагина и синхронизатору зеркала (tools/hermes_paths.py). Путь
# этой машины в коде держать нельзя — панель ставится и на другие компьютеры.
from hermes_paths import detect_hermes_home, hermes_home  # noqa: E402,F401


def _safe_segment(value: str, what: str) -> str:
    """Имя скилла/категории без разделителей пути — защита от ``../``."""
    text = (value or "").strip()
    if not text:
        raise ValueError(f"не задано поле «{what}»")
    if any(ch in text for ch in ("/", "\\", "..", ":", "*", "?", '"', "<", ">", "|")):
        raise ValueError(f"недопустимые символы в поле «{what}»: {text!r}")
    return text


def _safe_category(value: str) -> str:
    """Категория скилла — с проверкой каждого сегмента пути.

    Категория бывает вложенной (``mlops/evaluation``), поэтому слэш здесь
    разрешён — но только как разделитель уровней: ``..``, абсолютные пути и
    прочие выходы из ``skills/`` отсекаются по сегментам. Имена с точки или
    подчёркивания не берём: такие каталоги Hermes считает служебными и
    категориями не признаёт (``do_categories``).
    """
    text = (value or "").strip().strip("/")
    if not text:
        raise ValueError("не задано поле «категория»")
    parts = [part for part in text.split("/") if part]
    if not parts:
        raise ValueError("не задано поле «категория»")
    for part in parts:
        if part in (".", "..") or part.startswith((".", "_")):
            raise ValueError(f"недопустимый сегмент категории: {part!r}")
        _safe_segment(part, "категория")
    return "/".join(parts)


def _run(cmd: list[str], timeout: int = 180) -> dict:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=str(REPO))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "returncode": None}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "output": out[-4000:], "error": err[-2000:]}


# ── действия ─────────────────────────────────────────────────────────────────
def do_health() -> dict:
    return {
        "ok": True,
        "layer": "python-core (no LLM)",
        "repo": str(REPO),
        "staging": str(STAGING),
        "hermes_home": str(hermes_home()),
        "python": str(SKILL_PY),
        "drafts": [p.name for p in STAGING.iterdir() if p.is_dir()] if STAGING.is_dir() else [],
    }


def do_state() -> dict:
    """Состояние панели + история прогонов.

    Отчёт отдаётся ЦЕЛИКОМ (кроме ``preview`` — текст панель берёт маршрутом
    ``/text``), потому что по нему панель пишет заголовок блока «Результат
    разбора». Раньше уезжал только флаг ``has_report``, и панель собирала
    заголовок из ``history[0]`` — урезанной записи без ``junk_total``. Владелец:
    «в заголовке выводится одно и то же текстовое сообщение! Даже если разбор не
    произведён, не производился или был выполнен новый» — это оно: заголовок
    жил по истории, а не по отчёту.
    """
    st = load_state()
    rep = st.get("report") or {}
    light = {k: v for k, v in rep.items() if k != "preview"} if rep else {}
    return {
        "ok": True,
        "state": {k: v for k, v in st.items() if k not in ("report", "history")},
        "history": st.get("history") or [],
        "report": light,
        "report_at": st.get("report_at") or "",
        "report_src": (light.get("url") or ""),
        "last_error": st.get("last_error"),
        "has_report": bool(rep),
        # Сводка черновика — сразу в состоянии: блок «Черновик скилла» стоит
        # свёрнутым, и его заголовок обязан быть фактом уже при открытии панели,
        # как у «Результата разбора» (панель не должна ждать клика).
        "draft": do_draft(st.get("name") or "", st.get("src") or ""),
        # Ключ черновика = слаг источника. Панель по нему понимает, что черновик
        # принадлежит ИСТОЧНИКУ, а не имени скилла: правка имени его не теряет.
        "draft_key": _draft_key(st.get("src") or "", st),
        # Рабочие каталоги: что лежит рядом и что подлежит уборке. Панель называет
        # это вслух («черновик прежнего источника», «установлен - можно убрать»),
        # а не удаляет молча: удаление живёт отдельными маршрутами /drop и /prune.
        "drafts": do_drafts(st.get("src") or ""),
    }


def skills_root() -> Path:
    """Каталог скиллов профиля: ``skills/<категория>/<имя>/SKILL.md``."""
    return hermes_home() / "skills"


def do_categories() -> dict:
    """Существующие категории скиллов — панель выбирает ТОЛЬКО из них.

    Категория — не подпись, а механизм: по ней Hermes решает, когда скилл
    подгружать. Придуманная в поле ввода («csharp stuff») уводит скилл в
    сторону — агент не сопоставит его с текущей задачей и скилл не подхватится.
    Поэтому список берём с диска: категория = каталог, в котором уже лежит хотя
    бы один скилл (``SKILL.md``), либо пустой каталог-заготовка верхнего уровня
    (``networking``): папка есть — значит её должно быть видно и можно выбрать,
    иначе единственный способ завести в ней скилл — набрать имя руками.

    Плоские (``research``) и вложенные (``mlops/evaluation``) — одним списком,
    в том виде, в каком их ждёт установщик.

    К каждой категории отдаём её описание (``details``) — ровно в том состоянии,
    в каком его увидит агент:

    ``ok``             Hermes прочитает пояснение;
    ``no-frontmatter`` текст в файле есть, но без YAML-шапки Hermes его не берёт;
    ``no-file``        файла нет — в индексе скиллов категория идёт голым именем.

    Панель показывает это чипсой, чтобы «немая» категория не оставалась тайной.
    """
    root = skills_root()
    cats: set[str] = set()
    loose: list[str] = []
    if root.is_dir():
        # 1) категории по скиллам: скилл на глубине ≥2 задаёт свою категорию
        for skill_md in sorted(root.rglob("SKILL.md")):
            rel = skill_md.parent.relative_to(root)
            parts = rel.parts
            if not parts:               # SKILL.md прямо в skills/ — Hermes зовёт это «general»
                loose.append(skill_md.name)
                continue
            if any(part.startswith((".", "_")) for part in parts):
                continue
            # Категория — ровно та, что видит Hermes (`prompt_builder._build_snapshot_entry`):
            # `skills/<cat>/<имя>/SKILL.md` → `<cat>`; `skills/<cat>/SKILL.md` → тоже `<cat>`
            # (скилл носит имя каталога); глубже (`mlops/evaluation/<имя>`) → по сегментам.
            # Раньше такие каталоги считались «скиллами вне категорий»: они пропадали из
            # выпадашки, хотя Hermes показывает их категориями в промпте.
            cats.add(parts[0] if len(parts) <= 2 else "/".join(parts[:-1]))
        # 2) пустые каталоги: папка есть — её должно быть видно. Иначе networking
        #    в списке отсутствует, и завести в нём скилл можно только руками.
        for top in sorted(root.iterdir()):
            if not top.is_dir() or top.name.startswith((".", "_")):
                continue
            if (top / "SKILL.md").is_file():    # это скилл без категории, а не категория
                continue
            cats.add(top.name)
            # Вложенный уровень берём только по DESCRIPTION.md (заготовка) или по
            # скиллам внутри. Иначе служебные подпапки скилла (references, assets,
            # scripts) вылезли бы в списке категорий.
            for sub in sorted(top.iterdir()):
                if not sub.is_dir() or sub.name.startswith((".", "_")):
                    continue
                if (sub / "SKILL.md").is_file():   # skills/<категория>/<имя> — это сам скилл
                    continue
                if (sub / "DESCRIPTION.md").is_file() or any(sub.rglob("SKILL.md")):
                    cats.add(f"{top.name}/{sub.name}")
    details = {cat: read_category_desc(root, cat) | {
        "dir": str(root / cat),
        "exists": (root / cat).is_dir(),
        "skills": sum(1 for _ in (root / cat).rglob("SKILL.md")) if (root / cat).is_dir() else 0,
        "empty": not any((root / cat).rglob("SKILL.md")) if (root / cat).is_dir() else True,
    } for cat in sorted(cats)}
    return {"ok": True, "root": str(root), "count": len(cats),
            "categories": sorted(cats), "details": details, "loose": sorted(loose)}


def _frontmatter(text: str) -> dict:
    """Поля YAML-шапки файла — ровно те, что нужны DESCRIPTION.md.

    Полный YAML не тянем: Hermes читает из описания категории одно поле
    ``description``, а лишняя зависимость мешала бы переносить панель на другую
    машину.
    """
    body = (text or "").lstrip("\ufeff").lstrip()
    if not body.startswith("---"):
        return {}
    rest = body[3:]
    end = rest.find("\n---")
    if end < 0:
        return {}
    fields: dict[str, str] = {}
    for line in rest[:end].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def _plain_text(text: str, limit: int = 400) -> str:
    """Текст файла без YAML-шапки — панель показывает его человеку как есть."""
    body = (text or "").lstrip("\ufeff").lstrip()
    if body.startswith("---"):
        rest = body[3:]
        end = rest.find("\n---")
        if end >= 0:
            body = rest[end + 4:].strip()
    body = " ".join(body.split())
    return body[:limit] + ("…" if len(body) > limit else "")


def read_category_desc(root: Path, cat: str) -> dict:
    """Описание категории — так, как его УВИДИТ агент (и как его править).

    Hermes берёт поле ``description`` из frontmatter ``DESCRIPTION.md``
    (``agent/prompt_builder.py:_read_category_descriptions``) и вставляет рядом с
    именем категории в индекс скиллов. Проза без шапки для промпта невидима,
    поэтому состояние отдаём не «файл есть/нет», а «прочитается или нет»: панель
    показывает человеку написанный текст, но честно помечает случай, когда агент
    этого текста не увидит.
    """
    path = root / cat / "DESCRIPTION.md"
    if not path.is_file():
        return {"desc_state": "no-file", "desc": "", "desc_raw": "", "desc_path": str(path)}
    text = path.read_text(encoding="utf-8", errors="replace")
    desc = str(_frontmatter(text).get("description", "")).strip().strip("'\"")
    return {"desc_state": "ok" if desc else "no-frontmatter",
            "desc": desc, "desc_raw": _plain_text(text), "desc_path": str(path)}


def do_write_category_desc(cat: str, text: str = "", mode: str = "write",
                           force: bool = False) -> dict:
    """Записать ``DESCRIPTION.md`` категории — пояснение для агента.

    Текст сочиняет LLM (или человек в панели), ядро только приводит его к формату
    и пишет. Перенос строки внутри ``description`` сломал бы YAML, поэтому
    описание сжимаем в одну строку. Режим ``fix`` — для файла, где текст уже есть,
    но без шапки: прежнюю прозу сохраняем телом ниже шапки, ничего не теряем молча.
    """
    try:
        category = _safe_category(cat)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    wanted = (mode or "write").strip().lower()
    if wanted not in ("write", "fix"):
        return {"ok": False, "error": f"неизвестный режим: {mode!r}"}

    root = skills_root()
    cat_dir = root / category
    path = cat_dir / "DESCRIPTION.md"
    here = read_category_desc(root, category)
    one_line = " ".join((text or "").split())
    body = ""

    if path.is_file():
        if wanted == "fix":
            # Проза без шапки: то, что человек написал, остаётся телом, а в шапку
            # уходит то же самое одной строкой — Hermes её наконец видит.
            body = here["desc_raw"]
            one_line = one_line or body
        elif not force:
            return {"ok": False, "exists": True, "desc_state": here["desc_state"],
                    "desc": here["desc"] or here["desc_raw"], "path": str(path),
                    "error": "DESCRIPTION.md уже есть - нужен --force или режим fix"}
    if not one_line:
        return {"ok": False, "error": "пустое описание: нечего записывать"}

    cat_dir.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    parts = ["---", f"description: {one_line}", "---"]
    if body:
        parts += ["", body]
    path.write_text("\n".join(parts) + "\n", encoding="utf-8", newline="\n")
    saved = read_category_desc(root, category)
    return {"ok": saved["desc_state"] == "ok", "path": str(path), "created": created,
            "category": category, "mode": wanted, "desc": saved["desc"],
            "desc_state": saved["desc_state"], "kept_body": bool(body)}


def do_rerun(src: str = "", strat: str = "", mode: str = "",
             name: str = "", cat: str = "", depth: str = "", lang: str = "") -> dict:
    """Прогнать каскад по источнику: загрузка + очистка + отчёт. LLM не участвует."""
    if not (src or "").strip():
        return {"ok": False, "error": "пустой источник: нужен url или локальный путь"}
    st = load_state()
    st["src"] = src.strip()
    if strat:
        st["strat"] = strat.strip()
    for key, value in (("mode", mode), ("name", name), ("cat", cat),
                       ("depth", depth), ("lang", lang)):
        if value:
            st[key] = value.strip()
    started = time.time()
    report = run_fetch(st)
    took = round(time.time() - started, 2)
    ok = bool(report.get("ok"))
    result = {
        "ok": True,                       # запрос выполнен; ниже — что вышло
        "fetch_ok": ok,
        "source": st["src"],
        "strategy": report.get("strategy"),
        "strategy_asked": st.get("strat") or "auto",
        "seconds": took,
        "report": report,
        "report_at": st.get("report_at") or "",
        "message": summary_line(report),
        "warning": None if ok else explain_failure(st["src"], st.get("strat") or "", report),
    }
    result["headings"] = report.get("headings")
    result["coverage"] = report.get("coverage")
    # Ключ черновика этого источника (слаг) — по нему панель показывает, куда
    # ляжет черновик, и передаёт его в /draft и /install. Имя скилла в ключе не
    # участвует: его правят свободно, и черновик от этого не должен пропадать.
    result["draft_key"] = _draft_key(st.get("src") or "", st)
    # Имя скилла панель предлагает ПОСЛЕ разбора: в строке url тема часто не названа
    # («.../186253740.html»), а в заголовке страницы — названа. Это ПРЕДЛОЖЕНИЕ:
    # поле имени остаётся редактируемым, и ручной ввод оно не затирает.
    result["suggested_name"] = dash.suggest_skill_name(
        st.get("src") or "", report.get("title") or "", report.get("source_file") or "")
    return result


def do_resolve(src: str = "") -> dict:
    """Ключ черновика и предложенное имя — по ОДНОЙ строке источника, без сети.

    Панель зовёт это при вводе источника, ещё до разбора: ключ черновика обязан
    следовать за ПОЛЕМ, а не за прошлым прогоном ядра. Иначе после смены url блок
    «Черновик скилла» показывал файлы прежней работы (владелец: «ввёл новый url, а
    в блоке 3 содержимое предыдущего скилла»): ключ брался из ``/state``, где лежит
    последний прогон, и панель искала черновик не там, где смотрит человек.

    Заголовок страницы подставляем, только если этот источник уже разбирали (он
    лежит в отчёте): из одной строки url тема часто не читается. Имя здесь —
    догадка для поля, а не решение: его видно и правится руками.
    """
    asked = (src or "").strip()
    if not asked:
        return {"ok": True, "key": "", "suggested_name": ""}
    st = load_state()
    same = asked == str(st.get("src") or "").strip()
    rep = st.get("report") or {}
    return {
        "ok": True,
        "key": _draft_key(asked, st),
        "suggested_name": dash.suggest_skill_name(
            asked, (rep.get("title") or "") if same else "", ""),
    }


def _fetched_file(path: str = "") -> Path:
    """Файл очищенного текста: явный путь либо ``source_file`` последнего прогона.

    Показываем только то, что лежит внутри ``b2s_fetched``: панель не должна
    уметь прочитать произвольный файл с диска по строке из поля ввода.
    """
    raw = (path or "").strip()
    if not raw:
        rep = load_state().get("report") or {}
        raw = str(rep.get("source_file") or "")
    if not raw:
        raise ValueError("нечего показывать: сначала шаг 1 - разбор источника")
    target = Path(raw).resolve()
    if not target.is_file():
        raise ValueError(f"файл не найден: {target}")
    root = FETCH_DIR.resolve()
    if target != root and root not in target.parents:
        raise ValueError("файл лежит вне b2s_fetched - показываю только своё сырьё")
    return target


def do_text(path: str = "", offset: int = 0, limit: int = 0) -> dict:
    """Очищенный текст источника — то, что панель показывает после шага 1. Без LLM.

    ``limit=0`` — файл целиком; иначе окно ``offset…offset+limit``: панель сначала
    берёт первый экран и догружает остаток, а не тянет 60 КБ на каждый рендер.
    """
    try:
        target = _fetched_file(path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    text = target.read_text(encoding="utf-8", errors="replace")
    start = max(0, int(offset or 0))
    size = max(0, int(limit or 0))
    window = text[start:start + size] if size else text[start:]
    return {
        "ok": True,
        "path": str(target),
        "name": target.name,
        "chars": len(text),
        "lines": text.count("\n") + 1,
        "offset": start,
        "limit": size,
        "returned": len(window),
        "truncated": start + len(window) < len(text),
        "text": window,
    }


def _draft_dirs() -> list[Path]:
    """Каталоги черновиков в ``staging`` — по одному на скилл.

    Служебные (``_probe*``) и просто файлы (``merge-plan.json``) не считаем:
    черновик — это каталог, в котором есть ``SKILL.md`` (индекс) или ``chapters/``.
    """
    if not STAGING.is_dir():
        return []
    out: list[Path] = []
    for p in STAGING.iterdir():
        if not p.is_dir() or p.name.startswith((".", "_")):
            continue
        if (p / "SKILL.md").is_file() or (p / "chapters").is_dir():
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)


def _draft_key(src: str = "", st: dict | None = None) -> str:
    """Ключ черновика — слаг ИСТОЧНИКА, а не имя скилла.

    Имя скилла человек правит свободно: оно про то, как скилл назовётся в
    профиле. Черновик же принадлежит ИСТОЧНИКУ — пока источник тот же, черновик
    тот же. Поэтому каталог в ``staging`` называется слагом источника: тем же,
    которым ядро уже назвало файл в ``b2s_fetched`` (``<слаг>.md``). Иначе одно
    движение в поле «Имя скилла» уводило панель в несуществующий каталог, и на
    живом черновике показывалось «черновика нет» (владелец: «один чих - и заново
    делай черновик»).
    """
    state = st if st is not None else load_state()
    asked = (src or "").strip()
    rep = state.get("report") or {}
    # Источник тот же, что разбирали последним, — берём слаг, которым уже назван
    # файл источника: он гарантированно совпадает с `b2s_fetched`, даже если
    # правило слаг-ификации когда-нибудь поменяется.
    if asked and asked == str(state.get("src") or "").strip():
        stem = Path(str(rep.get("source_file") or "")).stem
        if stem:
            return stem
    if asked:
        return slug_for_url(asked)
    return Path(str(rep.get("source_file") or "")).stem


def _draft_meta_src(d: Path) -> str:
    """Источник черновика по его ``metadata.json`` ('' — если не записан).

    Именно по этому полю черновик опознаётся как «снят с того же источника»,
    когда каталог назван не слагом (наследие: каталог по имени скилла).
    """
    try:
        meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    src = meta.get("source")
    if isinstance(src, dict):
        src = src.get("url") or src.get("path") or ""
    return str(src or "").strip()


def _draft_lookup(name: str = "", src: str = "") -> tuple[Path, str]:
    """Каталог черновика и как его нашли: ``slug`` | ``name`` | ``newest``.

    Порядок не случаен: сперва источник (штатная связка), потом имя скилла
    (совместимость — черновики, снятые до перехода на слаги, лежат каталогом по
    имени), и лишь когда не задано ничего — самый свежий.

    Признак ``how`` возвращается, чтобы панель могла назвать вслух, почему
    показан именно этот каталог: искать черновик по имени — уже исключение.
    """
    dirs = _draft_dirs()
    # Ключ источника берём ТОЛЬКО из явного src: без него поведение прежнее (по имени
    # скилла или самый свежий), и чужое имя не подменяется черновиком последнего прогона.
    key = _draft_key(src) if (src or "").strip() else ""
    wanted = (name or "").strip()
    # 1. Каталог назван слагом источника - штатная связка. Смотрим ПУТЬ, а не список
    #    каталогов: _draft_dirs() намеренно прячет служебные (_probe*), но план и
    #    установка обязаны работать и с ними - на них стоят тесты ядра.
    if key and (STAGING / key).is_dir():
        return STAGING / key, "slug"
    # 2. Каталог назван иначе, но metadata.json говорит: черновик снят с ЭТОГО
    #    источника. Так находятся черновики, снятые до перехода на слаги
    #    (каталог по имени скилла): имя человек правит, а источник - нет.
    if key:
        for p in dirs:
            meta_src = _draft_meta_src(p)
            if meta_src and slug_for_url(meta_src) == key:
                return p, "meta"
    # 3. Наследие: каталог по имени скилла (и источник неизвестен, и метаданных нет).
    if wanted and (STAGING / wanted).is_dir():
        return STAGING / wanted, "name"
    for p in dirs:
        if wanted and p.name == wanted:
            return p, "name"
    # Ничего не нашли. Если известен источник — чужие цифры не подставляем:
    # панель скажет «для этого источника черновика нет», а не покажет объём
    # другого скилла.
    if key:
        raise ValueError("черновика для этого источника в staging нет - его пишет шаг 2")
    if wanted:
        raise ValueError(f"черновика «{wanted}» в staging нет - его пишет шаг 2")
    if not dirs:
        raise ValueError("в staging нет черновиков - черновик пишет шаг 2 (это работа LLM)")
    return dirs[0], "newest"


def _draft_dir(name: str = "", src: str = "") -> Path:
    """Каталог черновика — обёртка над ``_draft_lookup`` для старых вызовов."""
    return _draft_lookup(name, src)[0]


# ── уборка рабочего каталога (staging + сырьё) ───────────────────────────────
# Черновик принадлежит ИСТОЧНИКУ и живёт каталогом ``staging/<слаг>``, сырьё —
# файлами ``b2s_fetched/<слаг>.{md,txt,*.report.json}``. Ни то, ни другое долго
# никто не убирал: после установки каталог оставался второй копией скилла, а
# сырьё копилось молча (владелец: «теоретически - всё должно очищаться»).
# Правила уборки:
#   * молча не удаляем ничего: сначала называем, что уйдёт и почему;
#   * установленный черновик живёт TTL — это архив, а не мусор;
#   * НЕустановленные свежие черновики держим лимитом (как бэкапы скиллов);
#   * активный черновик (тот, с которым работают сейчас) не трогаем никогда;
#   * служебные ``_probe*`` не трогаем — на них стоят тесты ядра.
STAGING_KEEP = 5       # сколько НЕустановленных черновиков держим, кроме активного
STAGING_TTL_DAYS = 7   # сколько дней живёт установленный черновик, прежде чем уйдёт


def _draft_info(d: Path) -> dict:
    """Паспорт каталога черновика: источник, объём, когда трогали, установлен ли."""
    files = [p for p in d.rglob("*") if p.is_file()]
    mtimes = [p.stat().st_mtime for p in files] or [d.stat().st_mtime]
    meta = _read_json(d / "metadata.json")
    inst = meta.get("installed") if isinstance(meta.get("installed"), dict) else {}
    return {
        "key": d.name,
        "src": _draft_meta_src(d),
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(max(mtimes))),
        "probe": d.name.startswith("_"),
        "installed": bool(inst),
        "installed_at": str(inst.get("at") or ""),
        "installed_skill": str(inst.get("skill") or ""),
        "installed_target": str(inst.get("target") or ""),
    }


def _mark_installed(staging: Path, skill: str, cat: str, mode: str, target: Path) -> dict:
    """Отметить ЧЕРНОВИК, что он уже поставлен: по ней уборка отличает архив от хлама.

    Пишется в ``metadata.json`` самого черновика, а не скилла: скилл уезжает в
    профиль и живёт своей жизнью, черновик — временный. Без отметки уборка не
    отличает «поставлен, можно отпускать» от «ещё не поставлен, над ним работают».
    """
    meta_file = staging / "metadata.json"
    meta = _read_json(meta_file)
    meta["installed"] = {
        "skill": skill, "cat": cat, "mode": mode, "target": str(target),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return meta["installed"]


def _retitle_skill_md(staging: Path, skill: str) -> dict:
    """Поставить в шапку SKILL.md имя скилла, выбранное в панели.

    Почему это работа ядра, а не агента: черновик пишет LLM, а имя живёт в блоке
    ЗАПИСИ и выбирается уже после генерации. Если шапку не привести в соответствие,
    каталог ``skills/<категория>/<имя>/`` разъедется с ``name:`` внутри, и Hermes
    будет звать скилл чужим именем (в списке одно, в промпте другое).
    """
    md = staging / "SKILL.md"
    if not md.is_file() or not skill:
        return {"changed": False}
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return {"changed": False}
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return {"changed": False}
    head = m.group(1)
    if re.search(r"^name:\s*.*$", head, re.M):
        new_head = re.sub(r"^name:\s*.*$", "name: " + skill, head, count=1, flags=re.M)
    else:
        new_head = "name: " + skill + "\n" + head
    if new_head == head:
        return {"changed": False, "name": skill}
    md.write_text(text[:m.start(1)] + new_head + text[m.end(1):], encoding="utf-8")
    return {"changed": True, "name": skill}


# ── служебные файлы черновика и метка плагина ────────────────────────────────
# Каталог черновика - рабочая тетрадь, а не скилл: план раскладки по главам
# (`merge-plan.json`) пишет ядро, и в профиль ему ехать незачем. Прежний
# `copytree` копировал каталог целиком, поэтому служебный план лежал у всех
# установленных скиллов. В staging он остаётся (панель его читает), в скилл - нет.
_SERVICE_FILES = frozenset({"merge-plan.json"})

_STAMP_CREATOR = "BookToSkill"       # подпись плагина в шапке скилла
_STAMP_KEYS = ("creator", "created", "updated")


def _is_service_file(rel: str) -> bool:
    """Файл ядра внутри каталога черновика: в скилл не копируется."""
    return Path(rel).name in _SERVICE_FILES


def _stamp_hermes_meta(staging: Path, *, now: str = "") -> dict:
    """Пометить шапку SKILL.md служебной меткой: creator / created / updated.

    Зачем: по файлу видно, что скилл собран нашим плагином и когда он создан и
    последний раз перезалит. В контекст агента это НЕ идёт: Hermes кладёт в
    системный промпт только `name` и `description` (`agent/prompt_builder.py`,
    строка вида `- {name}: {desc}`), остальные ключи шапки читаются только при
    `skill_view`. Линза Hermes эти поля пропускает молча, но лишь внутри
    `metadata`: ключи ВЕРХНЕГО уровня она считает чужими и выдаёт WARN
    (замерено: `creator` сверху = 3 warning, в `metadata.hermes` = 0).

    `created` пишется один раз: повторная установка его не перезаписывает, иначе
    «когда скилл появился» теряется и остаётся только последнее обновление.
    """
    md = staging / "SKILL.md"
    if not md.is_file():
        return {"changed": False}
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return {"changed": False}
    m = re.match(r"^---\r?\n(.*?)\r?\n---", text, re.S)
    if not m:
        return {"changed": False}
    head = m.group(1)
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = head.splitlines()
    when = now or time.strftime("%Y-%m-%d %H:%M")
    stamp = {"creator": _STAMP_CREATOR, "created": when, "updated": when}

    herm = next((i for i, l in enumerate(lines) if re.match(r"^\s+hermes:\s*$", l)), -1)
    if herm >= 0:
        base = re.match(r"^(\s+)", lines[herm]).group(1)
        inner = base + "  "
        end = len(lines)
        for j in range(herm + 1, len(lines)):
            if lines[j].strip() and not lines[j].startswith(inner):
                end = j
                break
        block = lines[herm + 1:end]
        for key, val in stamp.items():
            at = next((k for k, l in enumerate(block)
                       if re.match(r"^" + re.escape(inner) + key + r":", l)), -1)
            if at < 0:
                block.append(f"{inner}{key}: {val}")
            elif key != "created":          # дату создания не переписываем
                block[at] = f"{inner}{key}: {val}"
        new_lines = lines[:herm + 1] + block + lines[end:]
    else:
        mi = next((i for i, l in enumerate(lines) if re.match(r"^metadata:\s*$", l)), -1)
        if mi < 0 and any(re.match(r"^metadata:\s*\S", l) for l in lines):
            # `metadata:` записан inline - текстовой правкой его не расширить,
            # а второй такой ключ YAML не примет. Лучше не трогать, чем испортить.
            return {"changed": False, "error": "metadata записан не блоком"}
        fresh = ["metadata:", "  hermes:"] + [f"    {k}: {v}" for k, v in stamp.items()]
        new_lines = lines[:mi] + fresh + lines[mi + 1:] if mi >= 0 else lines + fresh

    new_head = nl.join(new_lines)
    if new_head == head:
        return {"changed": False}
    md.write_text(text[:m.start(1)] + new_head + text[m.end(1):], encoding="utf-8")
    # Отчитываемся ФАКТИЧЕСКИМ содержимым шапки, а не тем, что собирались записать:
    # `created` при повторной установке остаётся прежним, и врать про «только что
    # поставили дату создания» нельзя - по этой метке судят о возрасте скилла.
    got = dict(re.findall(r"^\s+(creator|created|updated):\s*(.+?)\s*$", new_head, re.M))
    return {"changed": True, **{k: got.get(k, v) for k, v in stamp.items()}}


def _draft_age_days(info: dict) -> float:
    """Сколько дней черновик лежит без дела.

    Установленному черновику срок отсчитывается от УСТАНОВКИ (``installed.at``):
    после записи в профиль его файлы больше не меняются, а без этой точки отсчёта
    архив мог бы «омолодиться» любым касанием metadata. Неустановленному - по
    последнему касанию файлов.
    """
    when = str(info.get("installed_at") or info.get("mtime") or "")
    try:
        stamp = time.mktime(time.strptime(when, "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0
    return max(0.0, (time.time() - stamp) / 86400.0)


def _is_active_draft(key: str, src: str, active: str) -> bool:
    """Этот черновик — тот, с которым работают сейчас?

    Сверяем и имя каталога, и ИСТОЧНИК: каталоги, снятые до перехода на слаги,
    названы именем скилла, и по одному имени активный черновик в них не узнать —
    а уборка обязана его не тронуть (иначе «просто открыл панель» сносит работу).
    """
    if not active:
        return False
    return key == active or bool(src) and slug_for_url(src) == active


def _source_files(src: str, key: str) -> list[Path]:
    """Файлы сырья источника в ``b2s_fetched``: ``<слаг>.md|.txt`` и их отчёты."""
    stems = {s for s in (key, slug_for_url(src) if (src or "").strip() else "") if s}
    out: list[Path] = []
    for stem in sorted(stems):
        out += sorted(p for p in FETCH_DIR.glob(f"{stem}.*") if p.is_file())
    return out


def do_drafts(active_src: str = "") -> dict:
    """Черновики в ``staging`` + вердикт уборки по каждому — панель показывает список.

    Ядро только НАЗЫВАЕТ, что подлежит уборке; удаляет лишь явная команда
    (``do_drop_draft`` / ``do_prune_staging(apply=True)``): молчаливых удалений
    в панели нет, это её общий принцип.
    """
    active = _draft_key(active_src) if (active_src or "").strip() else ""
    infos = sorted((_draft_info(p) for p in _draft_dirs()),
                   key=lambda i: i["mtime"], reverse=True)
    fresh = [i for i in infos
             if not i["installed"] and not _is_active_draft(i["key"], i["src"], active)]
    over = {i["key"] for i in fresh[STAGING_KEEP:]}
    for i in infos:
        i["active"] = _is_active_draft(i["key"], i["src"], active)
        i["drop"] = (i["key"] in over) or (i["installed"]
                                           and _draft_age_days(i) > STAGING_TTL_DAYS)
        i["reason"] = ""
        if i["drop"]:
            i["reason"] = (f"установлен и лежит дольше {STAGING_TTL_DAYS} дн."
                           if i["installed"]
                           else f"сверх лимита {STAGING_KEEP} свежих черновиков")
    return {"ok": True, "dirs": infos, "active": active,
            "keep": STAGING_KEEP, "ttl_days": STAGING_TTL_DAYS,
            "droppable": [i["key"] for i in infos if i["drop"]],
            # «Прежний источник»: черновики, снятые не с того, что разбирают сейчас.
            # Панель называет их вслух — иначе человек не знает, что рядом лежит
            # готовый черновик другого источника.
            "others": [i for i in infos
                       if not i["probe"]
                       and not _is_active_draft(i["key"], i["src"], active)]}


def do_drop_draft(key: str = "", src: str = "", with_source: bool = True) -> dict:
    """Убрать рабочий каталог черновика — и, по умолчанию, его сырьё.

    Скилл в профиле не трогается вообще: черновик живёт в ``staging``, установка
    живёт в ``skills/``. Служебные ``_probe*`` защищены: на них стоят тесты ядра.
    """
    key = (key or "").strip()
    asked = (src or "").strip()
    if not key and asked:
        key = _draft_key(asked)
    if not key:
        return {"ok": False, "error": "нечего убирать: не назван ни каталог, ни источник"}
    if key.startswith("_"):
        return {"ok": False,
                "error": f"служебный каталог {key} не убираем: на нём стоят тесты ядра"}
    d = STAGING / key
    if not d.is_dir():
        if not asked:
            return {"ok": False, "error": f"каталога staging/{key} нет"}
        try:
            d, _how = _draft_lookup(src=asked)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
    files = sum(1 for p in d.rglob("*") if p.is_file())
    # Сырьё ищем по слагу каталога И по слагу источника: каталог мог быть назван
    # именем скилла (наследие), и тогда имя файла в b2s_fetched с ним не совпадает.
    src_files = _source_files(asked or _draft_meta_src(d), d.name) if with_source else []
    shutil.rmtree(d, ignore_errors=True)
    gone = not d.is_dir()
    for p in src_files:
        try:
            p.unlink()
        except OSError:
            pass
    return {"ok": gone, "dropped": d.name, "files": files,
            "source_files": [p.name for p in src_files if not p.exists()],
            "kept_source": bool(src_files) and not with_source,
            "staging": str(STAGING)}


def do_prune_staging(keep: int = STAGING_KEEP, ttl_days: int = STAGING_TTL_DAYS,
                     active_src: str = "", apply: bool = False) -> dict:
    """Убрать лишние черновики: установленные по TTL, свежие — сверх лимита.

    Без ``apply`` это только ПЛАН: что уйдёт и почему. Диск трогает лишь
    ``apply=True``, и то по названному списку — панель сначала показывает его
    человеку. Сырьё убранных черновиков уходит вместе с ними.
    """
    if keep < 0 or ttl_days < 0:
        return {"ok": False, "error": "лимит и TTL не могут быть отрицательными"}
    active = _draft_key(active_src) if (active_src or "").strip() else ""
    infos = sorted((_draft_info(p) for p in _draft_dirs()),
                   key=lambda i: i["mtime"], reverse=True)
    fresh = [i for i in infos
             if not i["installed"] and not _is_active_draft(i["key"], i["src"], active)]
    over = {i["key"] for i in fresh[keep:]}
    plan: list[dict] = []
    for i in infos:
        if _is_active_draft(i["key"], i["src"], active):
            continue
        if i["installed"]:
            if _draft_age_days(i) > ttl_days:
                plan.append({**i, "reason": f"установлен и лежит дольше {ttl_days} дн."})
        elif i["key"] in over:
            plan.append({**i, "reason": f"сверх лимита {keep} свежих черновиков"})
    dropped: list[str] = []
    src_gone: list[str] = []
    if apply:
        for item in plan:
            shutil.rmtree(STAGING / item["key"], ignore_errors=True)
            if not (STAGING / item["key"]).exists():
                dropped.append(item["key"])
                for p in _source_files(item.get("src") or "", item["key"]):
                    try:
                        p.unlink()
                        src_gone.append(p.name)
                    except OSError:
                        pass
    return {"ok": True, "plan": plan, "dropped": dropped, "source_files": src_gone,
            "applied": bool(apply), "keep": keep, "ttl_days": ttl_days, "active": active}


def _draft_file(dir_path: Path, rel: str = "") -> Path:
    """Файл ВНУТРИ каталога черновика — панель не читает произвольный путь.

    ``rel`` пустой → ``SKILL.md``. Выход за каталог (``..``, абсолютный путь)
    отсекаем по ``resolve()``: тем же приёмом, что ``_fetched_file``.
    """
    root = dir_path.resolve()
    rel = (rel or "").strip().replace("\\", "/").lstrip("/")
    if not rel:
        rel = "SKILL.md"
    if ".." in rel.split("/"):
        raise ValueError(f"путь вне черновика: {rel!r}")
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"путь вне черновика: {rel!r}")
    if not target.is_file():
        raise ValueError(f"файла нет в черновике: {rel}")
    return target


def _md_stat(path: Path) -> dict:
    """Объём файла черновика: строки, символы, слова — без чтения всего в память подолгу."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "lines": text.count("\n") + 1,
        "chars": len(text),
        "words": len(text.split()),
    }


def do_draft(name: str = "", src: str = "") -> dict:
    """Сводка черновика скилла из ``staging`` — по ней панель пишет заголовок блока.

    Блок черновика устроен как «Результат разбора»: свёрнут, а в заголовке —
    ФАКТ (файлы, главы, объём, время), по которому видно, надо ли вообще
    заглядывать внутрь. Поэтому ядро отдаёт именно цифры и список файлов, а не
    текст: текст живёт в ``do_draft_text`` — его панель тянет лишь по клику.

    Ничего не пишет: только читает staging. Пустой staging — не ошибка, а
    состояние ``has_draft: False`` («черновика ещё нет»), иначе панель показала
    бы красную аварию там, где всё нормально.

    Каталог ищется по СЛАГУ ИСТОЧНИКА (``src``), а имя скилла — лишь запасной
    ключ для черновиков, снятых до перехода на слаги. Поэтому правка имени в
    панели не «теряет» готовый черновик: он принадлежит источнику.
    """
    dirs = _draft_dirs()
    try:
        d, matched = _draft_lookup(name, src)
    except ValueError as exc:
        return {"ok": True, "has_draft": False, "error": str(exc),
                "drafts": [p.name for p in dirs], "key": _draft_key(src)}

    files: list[dict] = []
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in (".md", ".json"):
            continue
        rel = p.relative_to(d).as_posix()
        if rel == "merge-plan.json":
            continue        # служебный план раскладки, не содержимое скилла
        row = {"rel": rel, "kind": "index" if rel == "SKILL.md" else _kind(rel)}
        row.update(_md_stat(p))
        files.append(row)

    chapters = [f["rel"] for f in files if f["rel"].startswith("chapters/")]
    skill_md = d / "SKILL.md"
    skill: dict = {"frontmatter": False, "name": "", "description": ""}
    if skill_md.is_file():
        fm = _frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
        skill = {
            "frontmatter": bool(fm),
            "name": fm.get("name", ""),
            "description": fm.get("description", ""),
        }

    glossary = next((f for f in files if f["rel"].lower().endswith("glossary.md")), None)
    terms = 0
    if glossary:
        terms = sum(1 for line in (d / glossary["rel"]).read_text(
            encoding="utf-8", errors="replace").splitlines() if line.startswith("**"))

    newest = max((p.stat().st_mtime for p in d.rglob("*") if p.is_file()), default=0)
    return {
        "ok": True,
        "has_draft": True,
        "name": d.name,
        "key": d.name,
        "matched": matched,
        "dir": str(d),
        "at": (datetime.fromtimestamp(newest).strftime("%H:%M:%S") if newest else ""),
        "mtime": newest,
        "files": files,
        "counts": {
            "files": len(files),
            "chapters": len(chapters),
            "chars": sum(f["chars"] for f in files),
            "lines": sum(f["lines"] for f in files),
            "words": sum(f["words"] for f in files),
        },
        "chapter_list": chapters,
        "glossary_terms": terms,
        "skill": skill,
        "drafts": [p.name for p in dirs],
    }


def do_draft_text(name: str = "", path: str = "", offset: int = 0, limit: int = 0,
                  src: str = "") -> dict:
    """Текст файла черновика — то, что панель показывает по клику внутри блока.

    ``limit=0`` — файл целиком; иначе окно ``offset…offset+limit``: панель берёт
    первый экран и догружает остаток, а не тянет 60 КБ на каждый рендер.
    """
    try:
        d = _draft_dir(name, src)
        target = _draft_file(d, path)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    text = target.read_text(encoding="utf-8", errors="replace")
    start = max(0, int(offset or 0))
    size = max(0, int(limit or 0))
    window = text[start:start + size] if size else text[start:]
    return {
        "ok": True,
        "name": d.name,
        "path": str(target),
        "rel": target.relative_to(d).as_posix(),
        "chars": len(text),
        "lines": text.count("\n") + 1,
        "offset": start,
        "limit": size,
        "returned": len(window),
        "truncated": start + len(window) < len(text),
        "text": window,
    }


def do_skills() -> dict:
    """Существующие скиллы профиля — панель выбирает имя, а не придумывает его.

    Имя занято — это не повод отказать, это сигнал: источник доливается в
    существующий скилл (fold-in). Поэтому панели нужен список того, что уже
    стоит, — категория, число глав, объём. Выбор имени из списка сам включает
    режим дополнения, и повторяющихся имён не возникает.
    """
    root = skills_root()
    items: list[dict] = []
    if root.is_dir():
        for skill_md in sorted(root.rglob("SKILL.md")):
            folder = skill_md.parent
            rel = folder.relative_to(root)
            if any(part.startswith((".", "_")) for part in rel.parts):
                continue
            parts = rel.parts
            files = [p for p in folder.rglob("*") if p.is_file()]
            ch_dir = folder / "chapters"
            chapters = ([p.name for p in sorted(ch_dir.rglob("*")) if p.is_file()]
                        if ch_dir.is_dir() else [])
            items.append({
                "name": parts[-1],
                "category": "/".join(parts[:-1]),     # "" — скилл лежит в корне skills/
                "path": str(folder),
                "files": len(files),
                "chapters": len(chapters),
                "chapter_names": chapters[:60],
                "bytes": sum(p.stat().st_size for p in files),
            })
    items.sort(key=lambda it: it["name"].lower())
    return {"ok": True, "root": str(root), "count": len(items), "skills": items}


def _digest(path: Path) -> str:
    """Хеш содержимого — чтобы «тот же файл» отличать от «файл с тем же именем»."""
    import hashlib
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _plan_of(staging: Path, target: Path) -> dict:
    """Что случится с каждым файлом при доливе: добавлен / перезаписан / тот же / сохранён.

    ``keep`` — файлы, которые есть в скилле, но которых нет в черновике. Именно
    они делают слепое «замещение» опасным: старая глава останется рядом с новым
    индексом, и скилл начнёт противоречить сам себе.
    """
    added, overwrite, same = [], [], []
    for src in sorted(p for p in staging.rglob("*") if p.is_file()):
        rel = src.relative_to(staging).as_posix()
        if _is_service_file(rel):       # рабочая тетрадь ядра, а не файл скилла
            continue
        dst = target / rel
        if not dst.is_file():
            added.append(rel)
        elif _digest(src) == _digest(dst):
            same.append(rel)
        else:
            overwrite.append(rel)
    keep: list[str] = []
    if target.is_dir():
        for dst in sorted(p for p in target.rglob("*") if p.is_file()):
            rel = dst.relative_to(target).as_posix()
            if not (staging / rel).is_file():
                keep.append(rel)
    return {"added": added, "overwrite": overwrite, "same": same, "keep": keep}


# ── план по главам: что слить, что переписать, что добавить ───────────────────
# Файловый план (_plan_of) отвечает, что произойдёт с файлами, но не на вопрос
# владельца «что из нового источника слить со старыми главами, а что написать
# заново». Прозу пишет LLM, поэтому ядро делает детерминированную часть: для
# каждой главы черновика находит ближайшие по теме файлы скилла и показывает
# раскладку с причиной. Решение — за агентом, счёт — за Python.

_STOP = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по
только ее мне было вот от меня еще нет о из ему теперь когда даже ну вдруг ли
если уже или ни быть был него до вас нибудь опять уж вам ведь там потом себя
ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без
будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой
совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем
сказать всех никогда сегодня можно при наконец два об другой хоть после над
больше тот через эти нас про всего них какая много разве три эту моя впрочем
хорошо свою этой перед иногда лучше чуть том нельзя такой им более всегда
конечно всю между это the and for with that this from are was were will can
has have not but you your our their its into out use used using when which
what how all any may more most other than then them these they some such only
also over under about after before between during each same""".split())

_WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_\-]{3,}")
_HEAD = re.compile(r"^(#{1,3})\s+(.*)$", re.M)


def _words(text: str) -> list[str]:
    """Значимые слова: без стоп-слов, регистр не важен, короче четырёх букв — мимо."""
    return [m.group(0).lower() for m in _WORD.finditer(text)
            if m.group(0).lower() not in _STOP]


def _topic(path: Path, top: int = 25) -> dict:
    """Тема файла: заголовок, подзаголовки и словарь — по ним считается близость."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    heads = [h.strip() for _, h in _HEAD.findall(text)]
    counts: dict[str, int] = {}
    for word in _words(text):
        counts[word] = counts.get(word, 0) + 1
    terms = [w for w, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]]
    return {"title": heads[0] if heads else path.stem, "heads": heads[1:9],
            "terms": set(terms), "term_list": terms, "words": sum(counts.values())}


def _overlap(a: set[str], b: set[str]) -> float:
    """Доля общего: совпадение считаем от меньшего набора.

    Jaccard для «та же тема, другой текст» слишком строг — он топит похожие
    главы в 0.1, и слияние не предлагается вовсе.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _kind(rel: str) -> str:
    """Глава, часть справочника (глоссарий/паттерны/шпаргалка) или прочее."""
    name = rel.rsplit("/", 1)[-1].lower()
    if rel.startswith("chapters/"):
        return "chapter"
    if name.startswith(("glossary", "patterns", "cheatsheet")):
        return "part"
    return "other"


def _md_files(root: Path) -> list[Path]:
    """Markdown-файлы, кроме SKILL.md: индекс пересобирается всегда, он не глава."""
    return sorted(p for p in root.rglob("*.md")
                  if p.is_file() and p.name.lower() != "skill.md")


def _chapter_plan(staging: Path, target: Path, threshold: float = 0.35) -> dict:
    """Раскладка «новое против существующего»: слить, переписать или добавить.

    Совпадение пути — rewrite (файл тот же, содержимое другое). Иначе ищем файл
    того же типа с наибольшей близостью: выше порога — merge с указанием, во что
    сливать. Ниже — новая глава. Ничьё решение здесь не принимается: это карта.
    """
    old: dict[str, dict] = {}
    if target.is_dir():
        for path in _md_files(target):
            old[path.relative_to(target).as_posix()] = _topic(path)
    rows: list[dict] = []
    for path in _md_files(staging):
        rel = path.relative_to(staging).as_posix()
        new = _topic(path)
        row = {"file": rel, "kind": _kind(rel), "title": new["title"],
               "words": new["words"], "terms": new["term_list"][:12],
               "action": "add", "merge_into": "", "similarity": 0.0, "confidence": "",
               "why": ""}
        if rel in old:
            row.update(action="rewrite", merge_into=rel, similarity=1.0,
                       why="файл с таким именем уже есть в скилле")
        else:
            best, score = "", 0.0
            for orel, otopic in old.items():
                if _kind(orel) != row["kind"]:
                    continue
                value = round(0.7 * _overlap(new["terms"], otopic["terms"]) + 0.3 * _overlap(
                    set(" ".join(new["heads"]).lower().split()),
                    set(" ".join(otopic["heads"]).lower().split())), 3)
                if value > score:
                    best, score = orel, value
            row["similarity"] = score
            if best and score >= threshold:
                # Сильное пересечение — слияние почти наверняка; слабое — повод
                # посмотреть глазами, поэтому отделяем «надёжно» от «возможно».
                row.update(action="merge", merge_into=best,
                           confidence="high" if score >= 0.6 else "medium",
                           why=f"тема пересекается с {best} (близость {score:.2f})")
            elif best:
                row["why"] = f"ближайшее - {best}, но близость низкая ({score:.2f})"
            else:
                row["why"] = "в скилле нет файлов того же типа"
        rows.append(row)
    # Файл, в который предлагают слить новую главу, «останется как есть» только
    # на бумаге: при слиянии он меняется. Поэтому такие файлы идут в отдельный
    # список «будет дополнен» — иначе план обещает то, чего не будет.
    merged_into: dict[str, list[str]] = {}
    for row in rows:
        if row["action"] == "merge":
            merged_into.setdefault(row["merge_into"], []).append(row["file"])
    keep = [{"file": rel, "title": topic["title"], "words": topic["words"]}
            for rel, topic in old.items()
            if not (staging / rel).is_file() and rel not in merged_into]
    touched = [{"file": rel, "by": merged_into[rel]} for rel in sorted(merged_into)]
    counts = {a: sum(1 for r in rows if r["action"] == a) for a in ("add", "merge", "rewrite")}
    return {"chapters": rows, "keep": keep, "touched": touched, "counts": counts}


def _chapter_prompt(name: str, mode: str, plan: dict) -> str:
    """Готовая постановка для LLM: раскладка + что от него требуется.

    Текст намеренно без «служебных» формулировок — уходит в чат как обычный запрос.
    """
    lines = [f"Долив в скилл «{name}» (режим {mode}). Раскладка по файлам:"]
    for row in plan["chapters"]:
        if row["action"] == "merge":
            tail = "" if row.get("confidence") == "high" else " - пересечение слабое, смотри глазами"
            lines.append(f"- {row['file']} («{row['title']}») → слить в {row['merge_into']} "
                         f"(близость {row['similarity']:.2f}){tail}")
        elif row["action"] == "rewrite":
            lines.append(f"- {row['file']} («{row['title']}») → переписать существующий файл")
        else:
            lines.append(f"- {row['file']} («{row['title']}») → новая глава")
    if plan["touched"]:
        lines.append("Будут дополнены (слияние в существующий файл): " +
                     ", ".join(f"{row['file']} ← {', '.join(row['by'])}" for row in plan["touched"]))
    if plan["keep"]:
        lines.append("Остаются нетронутыми: " + ", ".join(k["file"] for k in plan["keep"]))
    lines.append("")
    lines.append("По каждой строке реши: слить в существующий файл (объединить, убрав дубли), "
                 "переписать целиком или добавить новой главой. Спорные случаи - на решение "
                 "владельца, с обоснованием.")
    return "\n".join(lines)


def do_chapter_plan(name: str, cat: str = "", mode: str = "auto",
                    save: bool = False, threshold: float = 0.35,
                    src: str = "") -> dict:
    """План долива по главам: раскладка + причина + постановка для LLM.

    Ничего не пишет в скилл. ``save`` кладёт план рядом с черновиком
    (``staging/<слаг источника>/merge-plan.json``), чтобы агент читал его файлом,
    а не из вывода команды.

    Каталог черновика ищется по источнику (``src``): имя скилла здесь — только
    цель записи. Так план не рассыпается от правки имени в панели.
    """
    try:
        skill = _safe_segment(name, "имя скилла")
        category = _safe_category(cat) if cat else dash.DEFAULT_CATEGORY
        staging, matched = _draft_lookup(skill, src)
    except ValueError as exc:
        return {"ok": False, "error": str(exc),
                "hint": "сначала шаг 2 - «Сделать черновик»"}
    target = hermes_home() / "skills" / category / skill
    target_exists = target.is_dir() and any(target.iterdir())
    wanted = (mode or "auto").strip().lower()
    if wanted == "auto":
        wanted = "append" if target_exists else "create"
    plan = _chapter_plan(staging, target, threshold)
    info = {
        "ok": True, "name": skill, "category": category, "mode": wanted,
        "staging": str(staging), "staging_key": staging.name, "matched": matched,
        "target": str(target), "target_exists": target_exists,
        "threshold": threshold, **plan,
        "prompt": _chapter_prompt(skill, wanted, plan),
        "saved": "",
    }
    if save:
        dest = staging / "merge-plan.json"
        dest.write_text(json.dumps({k: v for k, v in info.items() if k != "saved"},
                                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        info["saved"] = str(dest)
    return info


def _backup_target(target: Path, name: str) -> str:
    """Копия скилла перед правкой на месте. Без неё «замещение» необратимо.

    Метка — секундная, поэтому две правки подряд попали бы в один каталог и
    бэкапы слились бы в кашу; добавляем суффикс, пока имя не станет свободным.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_DIR / f"{name}-{stamp}"
    serial = 2
    while dest.exists():
        dest = BACKUP_DIR / f"{name}-{stamp}-{serial}"
        serial += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(target, dest)
    _prune_backups(name)
    return str(dest)


def _prune_backups(name: str, keep: int = BACKUP_KEEP) -> None:
    """Держим последние ``keep`` копий на скилл — история, а не свалка."""
    older = sorted(BACKUP_DIR.glob(f"{name}-*"), key=lambda p: p.name)
    for dead in older[:-keep] if len(older) > keep else []:
        shutil.rmtree(dead, ignore_errors=True)


def _source_entry(staging: Path) -> dict:
    """Источник черновика: из его ``metadata.json``, иначе — из последнего прогона каскада.

    Нужен именно доливу: без журнала вторая страница вливается «вслепую», и в
    скилле не остаётся следов, откуда взята та или иная глава.
    """
    meta_file = staging / "metadata.json"
    if meta_file.is_file():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        src = meta.get("source") if isinstance(meta, dict) else None
        if isinstance(src, dict) and (src.get("url") or src.get("path") or src.get("file")):
            return dict(src)
    rep = load_state().get("report") or {}
    url = str(rep.get("url") or "")
    return {
        "url": url,
        "path": "" if url else str(rep.get("source_file") or ""),
        "title": rep.get("title") or "",
        "strategy": rep.get("strategy") or "",
        "chars": rep.get("chars") or 0,
        "cleaned_file": rep.get("source_file") or "",
        "junk": rep.get("junk"),
    }


def _read_json(path: Path) -> dict:
    """JSON-файл как словарь: битый, чужой или отсутствующий — пустой словарь."""
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _source_key(entry: dict) -> str:
    """Чем источники отличаются друг от друга: адрес, а если его нет — файл."""
    return str(entry.get("url") or entry.get("path") or entry.get("file")
               or entry.get("cleaned_file") or "").strip()


def _existing_sources(target: Path) -> list[dict]:
    """Что уже внесено в скилл — панель показывает это рядом с режимом долива."""
    meta_file = target / "metadata.json"
    if not meta_file.is_file():
        return []
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    srcs = meta.get("sources") if isinstance(meta, dict) else None
    if not isinstance(srcs, list):
        one = meta.get("source") if isinstance(meta, dict) else None
        srcs = [one] if isinstance(one, dict) else []
    out: list[dict] = []
    for item in srcs:
        if not isinstance(item, dict):
            continue
        out.append({
            "src": _source_key(item),
            "title": item.get("title") or "",
            "installed_at": item.get("last_installed_at") or item.get("fetched_at") or "",
            "installs": int(item.get("installs") or 1),
        })
    return out


def _record_sources(target: Path, skill: str, mode: str, plan: dict,
                    backup: str, staging: Path, prior: dict | None = None) -> dict:
    """Дописать в ``metadata.json`` скилла, ЧТО и ИЗ ЧЕГО в него влито.

    ``prior`` — журнал целевого скилла, снятый ДО копирования файлов: долив копирует
    ``metadata.json`` черновика поверх целевого, и без этого снимка история источников
    стиралась бы при каждом доливе (проверено тестом: sources оставался равен 1).
    """
    meta_file = target / "metadata.json"
    meta = _read_json(meta_file)          # что лежит сейчас (возможно, версия черновика)
    prior = prior if isinstance(prior, dict) else {}
    for field in ("sources", "install_log", "source"):
        if field in prior:                # журнал скилла главнее: его не теряем
            meta[field] = prior[field]

    entry = _source_entry(staging)
    key = _source_key(entry)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    today = now[:10]

    sources = meta.get("sources")
    if not isinstance(sources, list):
        sources = []
        old = meta.get("source")          # скилл, собранный до журнала
        if isinstance(old, dict):
            sources.append(dict(old))
    known = next((item for item in sources
                  if isinstance(item, dict) and key and _source_key(item) == key), None)
    if known is None:
        known = {"first_installed_at": today}
        sources.append(known)
    known.update({k: v for k, v in entry.items() if v not in (None, "")})
    known["last_installed_at"] = today
    known["installs"] = int(known.get("installs") or 0) + 1

    log = meta.get("install_log")
    if not isinstance(log, list):
        log = []
    log.append({
        "at": now,
        "mode": mode,
        "source": key,
        "added": len(plan.get("added") or []),
        "overwritten": len(plan.get("overwrite") or []),
        "kept": len(plan.get("keep") or []),
        "backup": backup or "",
    })

    meta["skill"] = meta.get("skill") or skill
    meta["sources"] = sources
    meta["source"] = known               # обратная совместимость: «последний источник»
    meta["install_log"] = log[-30:]      # история установок, а не свалка
    meta_file.parent.mkdir(parents=True, exist_ok=True)
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    return {"file": str(meta_file), "source": key, "sources": len(sources),
            "installs": known["installs"], "mode": mode,
            "new_source": known["installs"] == 1}


def do_install(name: str, cat: str = "", confirm: bool = False,
               force: bool = False, mode: str = "auto",
               allow_overwrite: bool = True, cat_desc: str = "",
               src: str = "") -> dict:
    """Перенос черновика в ``skills/<категория>/<имя>/`` — с планом и бэкапом.

    Режим — не удобство, а предохранитель, поэтому он выбирается по состоянию цели:

    ``create``   цели нет — обычная установка;
    ``append``   цель есть — ДОЛИВ: новые файлы кладутся рядом, существующие
                 перезаписываются только с согласия (``allow_overwrite``), а то,
                 чего нет в черновике, остаётся как было;
    ``replace``  цель есть — полная замена: сначала бэкап, потом снос каталога и
                 копирование черновика целиком (прежний ``copytree`` лишь
                 НАКЛАДЫВАЛ файлы, поэтому старые главы выживали при новом индексе).

    ``cat_desc`` — описание НОВОЙ категории: ядро кладёт его в
    ``DESCRIPTION.md`` (тот самый файл, по которому Hermes понимает, зачем
    категория нужна). Существующее описание не затирается — для правки есть
    отдельная команда ``desc``.

    Без ``confirm`` — только план: что добавится, что перезапишется, что
    останется. Ничего не пишется.
    """
    try:
        skill = _safe_segment(name, "имя скилла")
        category = _safe_category(cat) if cat else dash.DEFAULT_CATEGORY
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    wanted = (mode or "auto").strip().lower()
    if wanted not in ("auto", "create", "append", "replace"):
        return {"ok": False, "error": f"неизвестный режим установки: {mode!r}"}
    if force and wanted == "auto":
        wanted = "replace"

    try:
        staging, matched = _draft_lookup(skill, src)
    except ValueError as exc:
        return {"ok": False, "error": str(exc),
                "hint": "сначала шаг 2 - «Сделать черновик» (это уже LLM, идёт в чате)"}
    if not staging.is_dir():
        return {"ok": False, "error": f"нет черновика: {staging}",
                "hint": "сначала шаг 2 - «Сделать черновик» (это уже LLM, идёт в чате)"}

    target = hermes_home() / "skills" / category / skill
    target_exists = target.is_dir() and any(target.iterdir())
    # Режим фиксируем ЯВНО: «auto» — это решение по состоянию цели, а не отдельный
    # режим. Иначе он и в отчёт, и в журнал установки попадает как «auto», и по
    # записи невозможно понять, что же реально сделали.
    if wanted == "auto":
        wanted = "append" if target_exists else "create"
    if wanted == "create" and target_exists:
        wanted = "append"

    files = sorted(p for p in staging.rglob("*") if p.is_file()
                   and not _is_service_file(p.relative_to(staging).as_posix()))
    total = sum(p.stat().st_size for p in files)
    skill_md = staging / "SKILL.md"
    plan = _plan_of(staging, target)
    counts = {k: len(v) for k, v in plan.items()}
    info = {
        "ok": True,
        "dry_run": not confirm,
        "name": skill,
        "category": category,
        "mode": wanted,
        "staging": str(staging),
        "staging_key": staging.name,
        "matched": matched,
        "target": str(target),
        "files": len(files),
        "bytes": total,
        "kb": round(total / 1024, 1),
        "target_exists": target_exists,
        "has_skill_md": skill_md.is_file(),
        "top_level": sorted(p.name for p in staging.iterdir())[:20],
        "plan": plan,
        "plan_counts": counts,
        "allow_overwrite": bool(allow_overwrite),
        # «Опасная зона»: есть что перезаписывать или что терять при замене
        "risk": bool(plan["overwrite"] or plan["keep"]),
        "warning": _install_warning(wanted, counts, target,
                                    cat_is_new=not (skills_root() / category).is_dir()),
        # что уже внесено: долив «органичен» только когда это видно до клика
        "existing_sources": _existing_sources(target),
        # Состояние категории: панель предупреждает о новой (её ещё нет) и о немой
        # (нет DESCRIPTION.md) ДО подтверждения — иначе скилл уедет в категорию,
        # которую агент не сопоставит с задачей.
        "category_exists": (skills_root() / category).is_dir(),
        "category_desc": read_category_desc(skills_root(), category),
    }
    if target_exists and not confirm:
        # Долив: к файловому плану добавляем раскладку по главам — что слить со
        # старыми, что написать заново. Решение принимает LLM, карту даёт ядро.
        chapter_plan = _chapter_plan(staging, target)
        info["chapters"] = chapter_plan["chapters"]
        info["chapter_counts"] = chapter_plan["counts"]
        info["chapter_keep"] = chapter_plan["keep"]
    if not skill_md.is_file():
        return {**info, "ok": False, "error": "в черновике нет SKILL.md"}
    if not confirm:
        return info

    backup = ""
    # Снимок журнала ДО копирования: черновик может принести свой metadata.json.
    prior = _read_json(target / "metadata.json")
    if target_exists:
        backup = _backup_target(target, skill)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Имя скилла выбрано в панели уже ПОСЛЕ генерации черновика (оно живёт в блоке
    # записи): приводим шапку SKILL.md в соответствие ДО копирования - иначе правка
    # осталась бы в staging, а в профиль уехала старая. Без этого каталог
    # skills/<категория>/<имя>/ разъезжается с `name:` внутри, и Hermes зовёт скилл
    # чужим именем (в списке одно, в промпте другое).
    retitle = _retitle_skill_md(staging, skill)
    stamp = _stamp_hermes_meta(staging)

    skip = shutil.ignore_patterns(*sorted(_SERVICE_FILES))
    wrote: list[str] = []
    if wanted == "replace":
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(staging, target, dirs_exist_ok=True, ignore=skip)
        wrote = sorted(p.relative_to(staging).as_posix() for p in files)
    elif wanted == "append":
        for rel in plan["added"]:
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staging / rel, dst)
            wrote.append(rel)
        if allow_overwrite:
            for rel in plan["overwrite"]:
                dst = target / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staging / rel, dst)
                wrote.append(rel)
    else:                                             # create
        shutil.copytree(staging, target, dirs_exist_ok=True, ignore=skip)
        wrote = sorted(p.relative_to(staging).as_posix() for p in files)

    # Описание категории пишем только там, где его ещё нет: чужой текст не
    # затираем (для правки существующего есть отдельная кнопка «Починить файл»).
    desc_written = ""
    if (cat_desc or "").strip():
        before = read_category_desc(skills_root(), category)
        if before["desc_state"] != "ok":
            res = do_write_category_desc(
                category, cat_desc,
                mode="fix" if before["desc_state"] == "no-frontmatter" else "write")
            if res.get("ok"):
                desc_written = res["path"]

    validation = _validate(target, skill_md)
    journal = _record_sources(target, skill, wanted, plan, backup, staging, prior=prior)
    # Отметка в самом черновике: он поставлен. По ней уборка отличает архив от
    # хлама, а панель может честно сказать «этот черновик уже установлен» и
    # предложить его убрать. Неудача отметки установку не валит: скилл-то лёг.
    try:
        installed_mark = _mark_installed(staging, skill, category, mode, target)
    except OSError as exc:
        installed_mark = {"error": str(exc)}
    return {**info, "dry_run": False, "installed": str(target), "backup": backup,
            "wrote": sorted(wrote), "kept": plan["keep"],
            "journal": journal,
            "installed_mark": installed_mark,
            "retitled": retitle,
            "stamped": stamp,
            "validation": validation,
            "category_desc_written": desc_written,
            "category_desc": read_category_desc(skills_root(), category),
            "ok": bool(validation.get("validate", {}).get("ok"))}


def _install_warning(mode: str, counts: dict, target: Path,
                     cat_is_new: bool = False) -> str:
    """Человеческая формулировка риска — её панель показывает перед подтверждением."""
    parts = []
    if counts.get("added"):
        parts.append(f"добавится файлов: {counts['added']}")
    if mode == "append" and counts.get("overwrite"):
        parts.append(f"перезапишется: {counts['overwrite']} (существующие файлы скилла)")
    if mode == "append" and counts.get("keep"):
        parts.append(f"останется как есть: {counts['keep']} (их нет в черновике)")
    if mode == "replace":
        if target.is_dir() and any(target.iterdir()):
            parts.append(f"каталог {target.name} будет снесён и заменён черновиком целиком")
        else:
            # Сносить нечего: режим выбран заранее, но цели ещё нет — говорим прямо,
            # иначе предупреждение пугает сносом того, что не существует.
            parts.append("каталога ещё нет - заменять нечего, будет обычная установка")
    known = _existing_sources(target)
    if known:
        parts.append(f"источников внесено ранее: {len(known)}")
    if cat_is_new:
        # Новая категория — не запрет, но агент подбирает скилл по категории, а
        # пояснение к ней (DESCRIPTION.md) читает из файла. Без файла категория
        # молчит — поэтому про это говорим ДО подтверждения, а не после.
        parts.append("категория новая: папка создастся, а вот DESCRIPTION.md у неё "
                     "не будет - Hermes покажет её без пояснения "
                     "(заполни поле «описание категории»)")
    if not parts:
        return ""
    return "Долив в существующий скилл: " + "; ".join(parts) + "." if mode == "append" \
        else "; ".join(parts).capitalize() + "."


def _validate(target: Path, skill_md: Path) -> dict:
    """Гейты форка: линза Hermes + security-скан сгенерированного скилла."""
    validate = _run([str(SKILL_PY), str(REPO / "tools" / "validate_skill.py"),
                     str(skill_md), "--lens", "hermes"])
    scan = _run([str(SKILL_PY), str(REPO / "tools" / "scan_generated_skill.py"),
                 str(target)])
    return {"validate": validate, "scan": scan}


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="book-to-skill core (no LLM)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="живо ли ядро и где что лежит")
    sub.add_parser("state", help="состояние панели + история прогонов")
    sub.add_parser("categories", help="категории скиллов + состояние их DESCRIPTION.md")

    p_rerun = sub.add_parser("rerun", help="загрузить источник и очистить текст")
    p_rerun.add_argument("--src", required=True)
    p_rerun.add_argument("--strat", default="")
    p_rerun.add_argument("--mode", default="")
    p_rerun.add_argument("--name", default="")
    p_rerun.add_argument("--cat", default="")
    p_rerun.add_argument("--depth", default="")
    p_rerun.add_argument("--lang", default="")

    p_resolve = sub.add_parser("resolve", help="ключ черновика (слаг) + имя по строке источника, без сети")
    p_resolve.add_argument("--src", required=True)

    p_text = sub.add_parser("text", help="очищенный текст источника (то, что видно в панели)")
    p_text.add_argument("--path", default="", help="файл; по умолчанию - последний прогон")
    p_text.add_argument("--offset", type=int, default=0)
    p_text.add_argument("--limit", type=int, default=0, help="0 = весь текст")

    p_draft = sub.add_parser("draft", help="сводка черновика в staging (заголовок блока панели)")
    p_draft.add_argument("--name", default="", help="имя скилла; по умолчанию - самый свежий черновик")
    p_draft.add_argument("--src", default="", help="источник: по нему ищется черновик (слаг), имя скилла - запасной ключ")

    p_dtext = sub.add_parser("draft-text", help="текст файла черновика (то, что видно в панели)")
    p_dtext.add_argument("--name", default="", help="имя скилла; по умолчанию - самый свежий черновик")
    p_dtext.add_argument("--src", default="", help="источник: по нему ищется черновик (слаг)")
    p_dtext.add_argument("--file", dest="rel", default="", help="файл внутри черновика; по умолчанию SKILL.md")
    p_dtext.add_argument("--offset", type=int, default=0)
    p_dtext.add_argument("--limit", type=int, default=0, help="0 = весь файл")

    p_inst = sub.add_parser("install", help="перенести черновик в skills/ (по умолчанию предпросмотр)")
    p_inst.add_argument("--name", required=True)
    p_inst.add_argument("--cat", default="")
    p_inst.add_argument("--confirm", action="store_true", help="реально писать на диск")
    p_inst.add_argument("--force", action="store_true",
                        help="алиас --mode replace: снести каталог и заменить целиком")
    p_inst.add_argument("--mode", default="auto", choices=["auto", "create", "append", "replace"],
                        help="auto: нет цели → create, есть → append (долив)")
    p_inst.add_argument("--cat-desc", dest="cat_desc", default="",
                        help="описание НОВОЙ категории: ляжет в DESCRIPTION.md "
                             "(без него Hermes покажет категорию без пояснения)")
    p_inst.add_argument("--no-overwrite", dest="allow_overwrite", action="store_false",
                        help="долив строго «только новое»: существующие файлы не трогать")
    p_inst.set_defaults(allow_overwrite=True)
    p_inst.add_argument("--src", default="",
                        help="источник: по нему ищется черновик (слаг), имя скилла - запасной ключ")

    sub.add_parser("skills", help="существующие скиллы профиля (имя + категория + главы)")

    p_plan = sub.add_parser("plan", help="план долива по главам: слить / переписать / добавить")
    p_plan.add_argument("--name", required=True)
    p_plan.add_argument("--cat", default="")
    p_plan.add_argument("--mode", default="auto", choices=["auto", "create", "append", "replace"])
    p_plan.add_argument("--threshold", type=float, default=0.35,
                        help="порог близости, с которого предлагается слияние")
    p_plan.add_argument("--save", action="store_true",
                        help="положить план в staging/<слаг источника>/merge-plan.json")
    p_plan.add_argument("--src", default="",
                        help="источник: по нему ищется черновик (слаг), имя скилла - запасной ключ")

    p_desc = sub.add_parser("desc", help="описание категории: что Hermes скажет агенту (DESCRIPTION.md)")
    p_desc.add_argument("--cat", required=True)
    p_desc.add_argument("--text", default="", help="текст описания (одной строкой)")
    p_desc.add_argument("--mode", default="write", choices=["write", "fix"],
                        help="fix - обернуть в frontmatter прозу, которую Hermes сейчас не видит")
    p_desc.add_argument("--force", action="store_true", help="перезаписать существующее описание")

    p_drafts = sub.add_parser("drafts", help="черновики в staging + вердикт уборки (что уйдёт)")
    p_drafts.add_argument("--src", default="",
                          help="источник, с которым работают сейчас: его черновик не трогаем")

    p_drop = sub.add_parser("drop", help="убрать рабочий каталог черновика (и его сырьё)")
    p_drop.add_argument("--key", default="", help="имя каталога в staging")
    p_drop.add_argument("--src", default="", help="либо источник: каталог найдётся по слагу")
    p_drop.add_argument("--keep-source", dest="with_source", action="store_false",
                        help="оставить сырьё в b2s_fetched")
    p_drop.set_defaults(with_source=True)

    p_prune = sub.add_parser("prune", help="убрать лишние черновики (без --apply - только план)")
    p_prune.add_argument("--keep", type=int, default=STAGING_KEEP,
                         help="сколько НЕустановленных черновиков держим, кроме активного")
    p_prune.add_argument("--days", type=int, default=STAGING_TTL_DAYS,
                         help="сколько дней живёт установленный черновик")
    p_prune.add_argument("--src", default="", help="активный источник: его черновик не трогаем")
    p_prune.add_argument("--apply", action="store_true", help="реально удалять (иначе план)")

    args = parser.parse_args(argv)
    if args.cmd == "health":
        out = do_health()
    elif args.cmd == "state":
        out = do_state()
    elif args.cmd == "categories":
        out = do_categories()
    elif args.cmd == "skills":
        out = do_skills()
    elif args.cmd == "rerun":
        out = do_rerun(args.src, args.strat, args.mode, args.name, args.cat,
                       args.depth, args.lang)
    elif args.cmd == "resolve":
        out = do_resolve(args.src)
    elif args.cmd == "text":
        out = do_text(args.path, args.offset, args.limit)
    elif args.cmd == "draft":
        out = do_draft(args.name, args.src)
    elif args.cmd == "draft-text":
        out = do_draft_text(args.name, args.rel, args.offset, args.limit, args.src)
    elif args.cmd == "plan":
        out = do_chapter_plan(args.name, args.cat, args.mode, args.save, args.threshold, args.src)
    elif args.cmd == "desc":
        out = do_write_category_desc(args.cat, args.text, args.mode, args.force)
    elif args.cmd == "drafts":
        out = do_drafts(args.src)
    elif args.cmd == "drop":
        out = do_drop_draft(args.key, args.src, args.with_source)
    elif args.cmd == "prune":
        out = do_prune_staging(args.keep, args.days, args.src, args.apply)
    else:
        out = do_install(args.name, args.cat, args.confirm, args.force,
                         args.mode, args.allow_overwrite, args.cat_desc, args.src)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
