#!/usr/bin/env python3
"""Аудит ВЛОЖЕНИЙ скилла: архивы в его папке и то, как они описаны в SKILL.md.

Зачем это первая часть проверки связей
--------------------------------------
В скиллы стали класть архивы с примерами (`assets/favorites.zip` и подобные). Хост
сам такие файлы НЕ перечисляет - агент узнаёт о них единственным способом: из текста
`SKILL.md`. Значит архив без описания - это файл, который для агента не существует,
хотя владелец положил его именно «чтобы Hermes воспользовался».

Поэтому ``/audit_links`` сначала проверяет вложения и только потом строит граф
перекрёстных ссылок:

* архив найден, но в ``SKILL.md`` про него ни слова - статус ``missing`` (чинится
  автодополнением при ``apply``);
* раздел есть, но сам архив в нём не назван - ``partial``;
* архив назван - ``ok``; отдельно перечисляются внутренние файлы, которые в описании
  не упомянуты (``unlisted``), - это подсказка для агента, а не приговор: владелец
  вправе переименовать распакованную копию по-своему.

Ничего не удаляется и не переписывается: ``apply`` только ДОПИСЫВАЕТ раздел вложения
там, где его нет. Существующий раздел не трогается - в нём живёт смысл, который писал
человек или агент, и затирать его машинной таблицей нельзя.
"""
from __future__ import annotations

import pathlib
import re
import zipfile

GUIDE_HEAD = re.compile(r"(?mi)^#{2,4}[^\n]*?(вложени|файлы скилла|assets|архив)")
GUIDE_TITLE = "## Файлы скилла (вложения)"
ZIP_SUFFIX = ".zip"
# Разумный предел: архив на сотни мегабайт в скилле - это уже не пример, а склад.
MAX_ZIP_BYTES = 64 * 1024 * 1024


def fix_name(name: str) -> str:
    """Имя файла ВНУТРИ архива: русские имена лежат в cp866, а zipfile отдаёт cp437."""
    try:
        return name.encode("cp437").decode("cp866")
    except (UnicodeEncodeError, UnicodeDecodeError, AttributeError):
        return str(name)


def skill_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    """Каталоги скиллов категории: подкаталоги с ``SKILL.md`` (глубже не ищем)."""
    return sorted(p for p in pathlib.Path(root).iterdir()
                  if p.is_dir() and (p / "SKILL.md").is_file())


def zip_entries(path: pathlib.Path) -> list[dict]:
    """Содержимое архива: имя (в исходной кодировке) и размер каждого файла."""
    out: list[dict] = []
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                rel = fix_name(info.filename).replace("\\", "/").lstrip("./")
                out.append({"rel": rel, "name": rel.split("/")[-1], "bytes": info.file_size})
    except (zipfile.BadZipFile, OSError) as exc:
        return [{"rel": "", "name": "", "bytes": 0, "error": f"{type(exc).__name__}: {exc}"}]
    return out


def _mentioned(text: str, needle: str) -> bool:
    """Упоминание имени в тексте: регистр не важен, обратные кавычки не мешают."""
    return needle.lower() in text.lower()


def check_skill(skill: pathlib.Path) -> dict:
    """Проверить один скилл: архивы в его папке против описания в SKILL.md."""
    md = skill / "SKILL.md"
    text = md.read_text(encoding="utf-8", errors="replace") if md.is_file() else ""
    zips = sorted(p for p in skill.rglob("*") if p.is_file() and p.suffix.lower() == ZIP_SUFFIX)
    has_guide = GUIDE_HEAD.search(text) is not None
    rows: list[dict] = []
    for zp in zips:
        entries = zip_entries(zp)
        broken = any(e.get("error") for e in entries)
        unlisted = [] if broken else [e["name"] for e in entries if not _mentioned(text, e["name"])]
        size = zp.stat().st_size
        if broken:
            status = "broken"
        elif not has_guide:
            status = "missing"
        elif not _mentioned(text, zp.name):
            status = "partial"
        elif unlisted:
            status = "ok-unlisted"
        else:
            status = "ok"
        rows.append({
            "zip": zp.relative_to(skill).as_posix(),
            "bytes": size,
            "over_limit": size > MAX_ZIP_BYTES,
            "status": status,
            "guide_head": has_guide,
            "files": [e["rel"] for e in entries],
            "files_count": 0 if broken else len(entries),
            "unlisted": unlisted,
        })
    worst = "ok"
    order = {"broken": 4, "missing": 3, "partial": 2, "ok-unlisted": 1, "ok": 0}
    for r in rows:
        if order[r["status"]] > order[worst]:
            worst = r["status"]
    return {
        "skill": skill.name,
        "zips": rows,
        "zips_count": len(rows),
        "guide_head": has_guide,
        "status": "none" if not rows else worst,
    }


def scan(root: pathlib.Path) -> dict:
    """Отчёт по вложениям всей категории + сводка «что требует внимания»."""
    skills = [check_skill(d) for d in skill_dirs(root)]
    with_zips = [s for s in skills if s["zips_count"]]
    broken = [{"skill": s["skill"], "zip": z["zip"], "error": z["files"]}
              for s in with_zips for z in s["zips"] if z["status"] == "broken"]
    need_guide = sorted(s["skill"] for s in with_zips if not s["guide_head"])
    need_naming = sorted(s["skill"] for s in with_zips
                         if s["guide_head"] and any(z["status"] == "partial" for z in s["zips"]))
    return {
        "skills_checked": len(skills),
        "skills_with_zips": sorted(s["skill"] for s in with_zips),
        "attachments_count": sum(s["zips_count"] for s in with_zips),
        "need_guide": need_guide,
        "need_naming": need_naming,
        "broken": broken,
        "details": with_zips,
        "step": "1. вложения (до графа ссылок)",
    }


def guide_for(skill: pathlib.Path, rows: list[dict]) -> str:
    """Раздел вложений, который ядро дописывает, когда его нет.

    Таблица фактов (имя, размер, содержимое) - то, что можно узнать машиной. Назначение
    файлов человеку и агенту дописывать вручную: придумывать его за автора нельзя.
    """
    lines = [GUIDE_TITLE, "",
             "В папке есть архивы; ядро перечислило их содержимое автоматически:",
             "",
             "| Архив | Размер | Внутри |",
             "|---|---|---|"]
    for r in rows:
        inside = ", ".join(f"`{n}`" for n in r["files"][:12]) or "(пусто)"
        if len(r["files"]) > 12:
            inside += f" и ещё {len(r['files']) - 12}"
        lines.append(f"| `{r['zip']}` | {r['bytes']} Б | {inside} |")
    lines += ["",
              "Назначение каждого файла опиши здесь же: агент узнаёт о вложениях только из",
              "этого раздела - хост список файлов не отдаёт.", ""]
    return "\n".join(lines)


def apply_guide(root: pathlib.Path) -> dict:
    """Дописать раздел вложений там, где архивы есть, а раздела нет. Существующий не трогаем."""
    written, kept, broken = [], [], []
    for d in skill_dirs(root):
        rep = check_skill(d)
        if not rep["zips_count"]:
            continue
        if rep["guide_head"]:
            kept.append(rep["skill"])
            continue
        # Битый архив не описываем: в таблице вышло бы «(пусто)», и битый файл выглядел
        # бы описанным. Сначала человек чинит архив - потом аудит пишет раздел.
        if rep["status"] == "broken":
            broken.append(rep["skill"])
            continue
        md = d / "SKILL.md"
        text = md.read_text(encoding="utf-8", errors="replace")
        md.write_text(text.rstrip() + "\n\n" + guide_for(d, rep["zips"]), encoding="utf-8", newline="\n")
        written.append(rep["skill"])
    return {"written": sorted(written), "kept": sorted(kept), "broken_skipped": sorted(broken)}
