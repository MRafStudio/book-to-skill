"""Живая проверка: описания и имена из шапки уходят в панель БЕЗ кавычек YAML.

Что проверяем (`tools/api.py`):

1. `do_draft` — заголовок блока черновика берёт `name`/`description` из шапки
   `SKILL.md`; кавычки YAML не часть значения, значит в панель идёт чистый текст.
   Раньше сюда шло сырьё (`"Use when …"` в чипсе), потому что правило снятия
   кавычек жило только в чтении описания категории.
2. `categories` — то же правило для `DESCRIPTION.md`: у обоих путей один
   хелпер `_fm_text`, и это самое место, где они могут разъехаться.
3. Кавычки снимаются только по краям: двоеточие внутри описания (то, ради чего
   кавычки в YAML и ставят) остаётся целым.

Профиль не трогается — песочница HERMES_HOME; каталог черновика в `staging/`
свой (`probe-desc` по слагу источника) и убирается за собой.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from book_to_skill.fetcher import slug_for_url  # noqa: E402

URL = "https://example.com/probe-quoted.html"
KEY = slug_for_url(URL)
DRAFT = REPO / "staging" / KEY
HOME = Path("D:/tmp/b2s-fakehome-desc")
SKILLS = HOME / "skills"

QUOTED = (
    "---\n"
    'name: "probe-desc"\n'
    'description: "Use when probing quoted frontmatter."\n'
    "---\n"
    "\n# Проба\n\nТело.\n"
)
SINGLE = (
    "---\n"
    "name: probe-desc\n"
    "description: 'Use when probing single quotes.'\n"
    "---\n"
    "\n# Проба\n\nТело.\n"
)
COLON = (
    "---\n"
    "name: probe-desc\n"
    'description: "Use when x: y happens."\n'
    "---\n"
    "\n# Проба\n\nТело.\n"
)
BARE = "---\nname: probe-desc\ndescription: Use when probing bare YAML.\n---\n\n# Проба\n"

failures: list[str] = []


def check(label: str, got, want) -> None:
    good = got == want
    print(f"  [{'ok ' if good else 'FAIL'}] {label}: {got!r}" + ("" if good else f" (ждали {want!r})"))
    if not good:
        failures.append(label)


def api(*args: str) -> dict:
    env = {**os.environ, "HERMES_HOME": str(HOME)}
    proc = subprocess.run([sys.executable, str(REPO / "tools" / "api.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(REPO), env=env)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        failures.append(f"{' '.join(args)}: не JSON — {proc.stdout[-300:]} {proc.stderr[-200:]}")
        return {}


def draft_with(text: str) -> dict:
    """Черновик с заданной шапкой — сводка, как её видит панель."""
    if DRAFT.exists():
        shutil.rmtree(DRAFT)
    DRAFT.mkdir(parents=True)
    (DRAFT / "SKILL.md").write_text(text, encoding="utf-8")
    return api("draft", "--src", URL)


print("== 0. подготовка")
if HOME.exists():
    shutil.rmtree(HOME)
(SKILLS / "probe-cat").mkdir(parents=True)
(SKILLS / "probe-cat" / "DESCRIPTION.md").write_text(
    '---\ndescription: "Скиллы про пробу — кавычки."\n---\n', encoding="utf-8")
check("слаг источника — имя каталога черновика", KEY, "example-com-probe-quoted-html")

print("== 1. do_draft: двойные кавычки в шапке")
d = draft_with(QUOTED)
check("черновик найден по слагу", d.get("has_draft"), True)
check("имя без кавычек", d["skill"]["name"], "probe-desc")
check("описание без кавычек", d["skill"]["description"], "Use when probing quoted frontmatter.")
check("фронтматтер виден как есть", d["skill"]["frontmatter"], True)

print("== 2. do_draft: одинарные кавычки")
d = draft_with(SINGLE)
check("одинарные сняты", d["skill"]["description"], "Use when probing single quotes.")

print("== 3. do_draft: кавычки не трогают внутренние двоеточия")
d = draft_with(COLON)
check("двоеточие внутри сохранено", d["skill"]["description"], "Use when x: y happens.")

print("== 4. do_draft: шапка без кавычек не портится")
d = draft_with(BARE)
check("чистое значение как было", d["skill"]["description"], "Use when probing bare YAML.")

print("== 5. do_draft: шапки нет — не выдумываем")
d = draft_with("# Проба без шапки\n")
check("черновик всё равно найден", d.get("has_draft"), True)
check("frontmatter отсутствует", d["skill"]["frontmatter"], False)
check("описание пустое", d["skill"]["description"], "")

print("== 6. categories: то же правило на описании категории")
cats = api("categories")
check("описание категории без кавычек", cats["details"]["probe-cat"]["desc"], "Скиллы про пробу — кавычки.")
check("состояние ok", cats["details"]["probe-cat"]["desc_state"], "ok")

print("== 7. уборка")
if DRAFT.exists():
    shutil.rmtree(DRAFT)
if HOME.exists():
    shutil.rmtree(HOME)
ok_clean = not DRAFT.exists() and not HOME.exists()
print(f"  [{'ok ' if ok_clean else 'FAIL'}] черновик и песочница убраны")
if not ok_clean:
    failures.append("уборка")

print()
if failures:
    print(f"ПРОВАЛЕНО проверок: {len(failures)}: {failures}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ")
