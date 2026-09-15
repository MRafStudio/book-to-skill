"""Живая проверка описаний категорий и «своей категории» (`tools/api.py`).

Что проверяем:

1. `categories` отдаёт не только имена, но и СОСТОЯНИЕ описания — потому что
   описание категории идёт в промпт агента, и «файл есть, но без frontmatter»
   для Hermes равно «описания нет». Панель должна это различать.
2. `desc` пишет DESCRIPTION.md: прозу без шапки режим `fix` оборачивает в
   frontmatter, сохраняя текст телом, а существующее описание без `force` не
   трогает (чужой текст молча не затираем).
3. Установка умеет НОВУЮ категорию (в т.ч. вложенную `mlops/evaluation`) и
   заодно пишет её описание; без описания предупреждает до подтверждения.
4. Враждебный ввод не выводит запись за пределы `skills/`.

Профиль не трогается — песочница HERMES_HOME.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOME = Path("D:/tmp/b2s-fakehome-cat")
SKILLS = HOME / "skills"
STAGING = REPO / "staging" / "probe-cat"

FRONT_OK = ("---\ndescription: Скиллы про python — проба.\n---\n")
PROSE = "Apple / macOS skills — tools that interact with the Mac desk.\n"

failures: list[str] = []


def check(label: str, got, want) -> None:
    good = got == want
    print(f"  [{'ok ' if good else 'FAIL'}] {label}: {got!r}" + ("" if good else f" (ждали {want!r})"))
    if not good:
        failures.append(label)


def ok(label: str, cond: bool, note: str = "") -> None:
    print(f"  [{'ok ' if cond else 'FAIL'}] {label}{' — ' + note if note else ''}")
    if not cond:
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


def skill_md(name: str) -> str:
    return (f"---\nname: {name}\ndescription: Use when probing. Fixture.\n---\n\n# {name}\n\nПроба.\n")


def setup() -> None:
    if HOME.exists():
        shutil.rmtree(HOME)
    # Категория с рабочим описанием
    (SKILLS / "software-development" / "python-pathlib").mkdir(parents=True)
    (SKILLS / "software-development" / "python-pathlib" / "SKILL.md").write_text(
        skill_md("python-pathlib"), encoding="utf-8")
    (SKILLS / "software-development" / "DESCRIPTION.md").write_text(FRONT_OK, encoding="utf-8")
    # Категория с прозой без frontmatter — Hermes её не видит
    (SKILLS / "apple" / "notes").mkdir(parents=True)
    (SKILLS / "apple" / "notes" / "SKILL.md").write_text(skill_md("notes"), encoding="utf-8")
    (SKILLS / "apple" / "DESCRIPTION.md").write_text(PROSE, encoding="utf-8")
    # Категория вообще без описания
    (SKILLS / "blank" / "thing").mkdir(parents=True)
    (SKILLS / "blank" / "thing" / "SKILL.md").write_text(skill_md("thing"), encoding="utf-8")
    # Вложенная категория
    (SKILLS / "mlops" / "evaluation" / "weights").mkdir(parents=True)
    (SKILLS / "mlops" / "evaluation" / "weights" / "SKILL.md").write_text(
        skill_md("weights"), encoding="utf-8")
    # Одиночный каталог со SKILL.md внутри: для Hermes это КАТЕГОРИЯ, а скилл носит
    # её же имя (`_build_snapshot_entry`: category = parts[0]) — значит каталог обязан
    # быть в списке, иначе часть профиля для UI просто не существует.
    (SKILLS / "loose-skill").mkdir(parents=True)
    (SKILLS / "loose-skill" / "SKILL.md").write_text(skill_md("loose-skill"), encoding="utf-8")
    # Настоящий сирота: SKILL.md прямо в skills/ — Hermes кладёт его в «general»,
    # категории у него нет и выбирать в панели нечего.
    (SKILLS / "SKILL.md").write_text(skill_md("orphan"), encoding="utf-8")
    # Служебный каталог — категорией не считается
    (SKILLS / ".hidden" / "x").mkdir(parents=True)
    (SKILLS / ".hidden" / "x" / "SKILL.md").write_text(skill_md("x"), encoding="utf-8")
    # Пустая категория-заготовка: папка есть — значит её должно быть видно в списке
    # (иначе скилл в неё можно завести только набрав имя руками)
    (SKILLS / "networking").mkdir(parents=True)
    # Вложенная заготовка с описанием — тоже категория
    (SKILLS / "mlops" / "models").mkdir(parents=True)
    (SKILLS / "mlops" / "models" / "DESCRIPTION.md").write_text(FRONT_OK, encoding="utf-8")
    # Служебная подпапка скилла — категорией НЕ считается (иначе в списке
    # вылезут references/assets/scripts каждого скилла)
    (SKILLS / "software-development" / "python-pathlib" / "references").mkdir(parents=True)
    (SKILLS / "software-development" / "python-pathlib" / "references" / "api.md").write_text(
        "x", encoding="utf-8")
    # Черновик для установки
    if STAGING.exists():
        shutil.rmtree(STAGING)
    STAGING.mkdir(parents=True)
    (STAGING / "SKILL.md").write_text(skill_md("probe-cat"), encoding="utf-8")
    # Маркер готовности: без него ядро (и это правильно) не пускает черновик в
    # профиль - «каталог найден» больше не значит «черновик написан».
    (STAGING / "READY.json").write_text(
        '{"ready": true, "by": "test", "at": "2026-09-15T00:00:00"}\n', encoding="utf-8")


print("== 1. categories: состояние описания каждой категории")
setup()
cats = api("categories")
check("категорий найдено", cats.get("count"), 8)
check("служебный .hidden не категория", ".hidden/x" in cats.get("categories", []), False)
check("каталог со SKILL.md внутри — категория, как считает Hermes",
      "loose-skill" in cats.get("categories", []), True)
check("только SKILL.md прямо в skills/ — сирота", cats.get("loose"), ["SKILL.md"])
check("вложенная категория видна", "mlops/evaluation" in cats.get("categories", []), True)
check("описание читается", cats["details"]["software-development"]["desc_state"], "ok")
check("файла нет", cats["details"]["blank"]["desc_state"], "no-file")
check("проза без шапки", cats["details"]["apple"]["desc_state"], "no-frontmatter")
ok("проза apple видна человеку", "Apple" in cats["details"]["apple"]["desc_raw"])
check("счёт скиллов в категории", cats["details"]["software-development"]["skills"], 1)
# Пустой каталог: папка есть — её видно, и видно, что она пустая
check("пустая категория в списке", "networking" in cats.get("categories", []), True)
check("пустая категория помечена", cats["details"]["networking"]["empty"], True)
check("пустая категория без описания", cats["details"]["networking"]["desc_state"], "no-file")
check("категория со скиллами не пустая", cats["details"]["software-development"]["empty"], False)
check("вложенная заготовка с описанием видна", "mlops/models" in cats.get("categories", []), True)
check("служебная подпапка скилла не категория",
      "software-development/python-pathlib/references" in cats.get("categories", []), False)
check("references не попал в details",
      "software-development/python-pathlib/references" in cats.get("details", {}), False)

print("== 2. desc: запись описания")
made = api("desc", "--cat", "blank", "--text", "Пустая категория — сюда кладём пробы.")
check("записано", made.get("ok"), True)
check("состояние ok", made.get("desc_state"), "ok")
check("файл создан", made.get("created"), True)
text = (SKILLS / "blank" / "DESCRIPTION.md").read_text(encoding="utf-8")
ok("frontmatter на месте", text.startswith("---\ndescription: Пустая категория"), text[:60])

again = api("desc", "--cat", "blank", "--text", "другое описание")
check("повтор без --force не пишет", again.get("ok"), False)
check("причина — файл есть", again.get("exists"), True)
ok("прежний текст цел", "Пустая категория" in (SKILLS / "blank" / "DESCRIPTION.md").read_text(encoding="utf-8"))

print("== 3. desc --mode rewrite: переСОЗДАНИЕ заменяет прежнее, --force не нужен")
remade = api("desc", "--cat", "blank", "--mode", "rewrite",
             "--text", "Пустая категория - пересоздано.")
check("пересоздано", remade.get("ok"), True)
check("состояние ok", remade.get("desc_state"), "ok")
check("файла не создавали заново", remade.get("created"), False)
check("прежний текст телом не тащим", remade.get("kept_body"), False)
blank = (SKILLS / "blank" / "DESCRIPTION.md").read_text(encoding="utf-8")
ok("новый текст в шапке", "пересоздано" in blank, blank[:90])
ok("старого текста в файле нет", "сюда кладём пробы" not in blank, blank[:90])

print("== 4. desc --mode fix: прозу наконец видит Hermes")
fixed = api("desc", "--cat", "apple", "--mode", "fix",
            "--text", "Apple / macOS skills — tools that interact with the Mac desk.")
check("починено", fixed.get("ok"), True)
check("состояние ok", fixed.get("desc_state"), "ok")
check("прежний текст сохранён телом", fixed.get("kept_body"), True)
apple = (SKILLS / "apple" / "DESCRIPTION.md").read_text(encoding="utf-8")
ok("шапка добавлена", apple.startswith("---\ndescription: Apple"), apple[:60])
ok("проза осталась в теле", "interact with the Mac desk" in apple.split("---", 2)[2])

print("== 5. враждебный ввод за пределы skills/ не выходит")
bad = api("desc", "--cat", "../evil", "--text", "x")
check("выход наверх отвергнут", bad.get("ok"), False)
ok("ничего не создано выше skills", not (HOME / "evil").exists())
bad2 = api("desc", "--cat", ".hidden", "--text", "x")
check("служебное имя отвергнуто", bad2.get("ok"), False)

print("== 5. установка: своя категория + её описание")
dry = api("install", "--name", "probe-cat", "--cat", "brand-new")
check("новая категория видна в плане", dry.get("category_exists"), False)
check("dry-run ничего не создал", (SKILLS / "brand-new").exists(), False)
ok("предупреждение про немую категорию", "категория новая" in (dry.get("warning") or ""), dry.get("warning"))

real = api("install", "--name", "probe-cat", "--cat", "brand-new",
           "--cat-desc", "Новая категория — пробы установки.", "--confirm")
check("установлено", real.get("ok"), True)
check("описание записано", real.get("category_desc", {}).get("desc_state"), "ok")
ok("DESCRIPTION.md на диске", (SKILLS / "brand-new" / "DESCRIPTION.md").is_file())
check("скилл на месте", (SKILLS / "brand-new" / "probe-cat" / "SKILL.md").is_file(), True)

nested = api("install", "--name", "probe-cat", "--cat", "mlops/evaluation")
check("вложенная категория принимается", nested.get("category"), "mlops/evaluation")

keep = api("install", "--name", "probe-cat", "--cat", "software-development",
           "--cat-desc", "ЭТО НЕ ДОЛЖНО ПОПАСТЬ В ФАЙЛ", "--confirm")
check("долив прошёл", keep.get("ok"), True)
ok("существующее описание не затёрто",
   "ЭТО НЕ ДОЛЖНО" not in (SKILLS / "software-development" / "DESCRIPTION.md").read_text(encoding="utf-8"))

if STAGING.exists():
    shutil.rmtree(STAGING)

print()
if failures:
    print(f"ПРОВАЛЕНО проверок: {len(failures)}: {failures}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ")
