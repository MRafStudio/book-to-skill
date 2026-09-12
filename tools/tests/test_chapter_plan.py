"""Живая проверка плана долива по главам (`tools/api.py`: do_chapter_plan).

Сценарий: черновик доливается в существующий скилл. Ожидаем раскладку, которая
отвечает на вопрос владельца «что слить со старыми главами, что переписать, что
добавить»: совпал путь → rewrite, совпала тема → merge с указанием, во что
сливать, тема новая → add, старый файл, которого нет в черновике → keep.

Отдельно проверяем главное обещание: план НИЧЕГО не пишет в скилл (файлы цели
побитово те же после вызова), а сам план по флагу save ложится рядом с черновиком.
Профиль не трогается — песочница HERMES_HOME.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]   # tools/tests/ → корень форка
HOME = Path("D:/tmp/b2s-fakehome-plan")
SKILL = "_probe_plan"
STAGING = REPO / "staging" / SKILL
TARGET = HOME / "skills" / "software-development" / SKILL
SKILL_MD = ("---\nname: _probe_plan\ndescription: Use when probing the chapter plan. Fixture.\n---\n\n"
            "# Probe\n\nРаскладка по главам.\n")

# Тексты намеренно разные по теме: «каталоги» против «лицензии».
SAME_TOPIC = ("# Глава: Чтение каталогов\n\nКаталог читается перебором записей: scandir отдаёт "
              "элементы каталога, обход вложенных каталогов делается рекурсивно, кодировка имён "
              "файлов берётся из системы. Каталог каталог записи элементов обхода.\n")
OTHER_TOPIC = ("# Глава: Лицензия и авторские права\n\nЛицензия разрешает использование документации "
               "с указанием авторства, товарные знаки принадлежат владельцу, распространение "
               "допустимо при сохранении уведомления об авторских правах.\n")
NEW_TOPIC = ("# Глава: Асинхронные сокеты\n\nСокет асинхронный: цикл событий ожидает готовность "
             "дескриптора, буферизация записи ограничена, таймаут соединения задаётся параметром.\n")

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}" + ("" if ok else f" (ждали {want!r})"))
    if not ok:
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
    if proc.returncode != 0 and not proc.stdout.strip():
        raise SystemExit(f"api.py {args} упал: {proc.stderr[-800:]}")
    return json.loads(proc.stdout)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def digest(root: Path) -> dict:
    """Хеши всех файлов каталога — «изменилось ли хоть что-то»."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    return out


def draft() -> None:
    """Черновик: одна тема совпадает со старой главой, два пути совпадают, одна тема новая."""
    if STAGING.exists():
        shutil.rmtree(STAGING)
    write(STAGING / "SKILL.md", SKILL_MD)
    write(STAGING / "chapters" / "ch01-paths.md", SAME_TOPIC)            # тема как у ch07 старого
    write(STAGING / "chapters" / "ch02-license.md", OTHER_TOPIC)         # путь совпал → rewrite
    write(STAGING / "chapters" / "ch03-sockets.md", NEW_TOPIC)           # тема новая → add
    write(STAGING / "glossary.md", "# Глоссарий\n\nКаталог — перебор записей.\n")


def old_skill() -> None:
    """Существующий скилл: перезаписываемый файл, тематический двойник и лишний файл."""
    if TARGET.exists():
        shutil.rmtree(TARGET)
    write(TARGET / "SKILL.md", SKILL_MD)
    write(TARGET / "chapters" / "ch02-license.md", "# Глава: Лицензия\n\nстарое\n")   # rewrite
    write(TARGET / "chapters" / "ch07-directories.md", SAME_TOPIC)                    # merge-цель
    write(TARGET / "chapters" / "ch09-legacy.md", "# Глава: Наследие\n\nостаётся\n")  # keep
    write(TARGET / "glossary.md", "# Глоссарий\n\nКаталог — перебор записей.\n")      # rewrite


def row(out: dict, name: str) -> dict:
    for item in out.get("chapters", []):
        if item["file"] == name:
            return item
    raise AssertionError(f"в раскладке нет строки {name}: {[r['file'] for r in out.get('chapters', [])]}")


def main() -> int:
    for path in (HOME, STAGING):
        if path.exists():
            shutil.rmtree(path)
    HOME.mkdir(parents=True)
    draft()
    old_skill()
    before = digest(TARGET)

    print("1) план долива: путь совпал → rewrite, тема совпала → merge, тема новая → add")
    out = api("plan", "--name", SKILL, "--cat", "software-development")
    check("ok", out.get("ok"), True)
    check("mode", out.get("mode"), "append")
    check("target_exists", out.get("target_exists"), True)
    check("rewrite: ch02-license", row(out, "chapters/ch02-license.md")["action"], "rewrite")
    check("rewrite: glossary", row(out, "glossary.md")["action"], "rewrite")
    merged = row(out, "chapters/ch01-paths.md")
    check("merge: ch01-paths → старая глава", merged["action"], "merge")
    check("merge_into", merged["merge_into"], "chapters/ch07-directories.md")
    ok("merge: близость выше порога", merged["similarity"] >= out["threshold"],
       f"{merged['similarity']} ≥ {out['threshold']}")
    check("merge: надёжность", merged["confidence"], "high")
    check("add: ch03-sockets", row(out, "chapters/ch03-sockets.md")["action"], "add")
    ok("add: причина указана", bool(row(out, "chapters/ch03-sockets.md")["why"]),
       row(out, "chapters/ch03-sockets.md")["why"])
    check("counts", out.get("counts"), {"add": 1, "merge": 1, "rewrite": 2})
    check("keep: чужой лишний файл остаётся как есть", [k["file"] for k in out.get("keep", [])],
          ["chapters/ch09-legacy.md"])
    check("touched: цель слияния не попала в keep",
          [(r["file"], r["by"]) for r in out.get("touched", [])],
          [("chapters/ch07-directories.md", ["chapters/ch01-paths.md"])])

    print("2) постановка для LLM — файлом, а не «на словах»")
    prompt = out.get("prompt", "")
    ok("промпт непустой", len(prompt) > 200, f"{len(prompt)} символов")
    ok("промпт называет цель слияния", "слить в chapters/ch07-directories.md" in prompt)
    ok("промпт называет новую главу", "новая глава" in prompt)
    ok("промпт предупреждает о дополнении файла", "Будут дополнены" in prompt)

    print("3) порог управляет слиянием")
    strict = api("plan", "--name", SKILL, "--cat", "software-development", "--threshold", "0.99")
    check("строгий порог: слияний нет", strict["counts"]["merge"], 0)
    check("строгий порог: строка стала новой", row(strict, "chapters/ch01-paths.md")["action"], "add")

    print("4) план НЕ пишет в скилл (главное обещание)")
    saved = api("plan", "--name", SKILL, "--cat", "software-development", "--save")
    ok("save вернул путь", str(saved.get("saved", "")).endswith("merge-plan.json"), saved.get("saved", ""))
    plan_file = STAGING / "merge-plan.json"
    ok("план лежит рядом с черновиком", plan_file.is_file())
    stored = json.loads(plan_file.read_text(encoding="utf-8"))
    check("в файле — та же раскладка", stored["counts"], {"add": 1, "merge": 1, "rewrite": 2})
    ok("в файле есть раскладка, но нет служебного поля saved", "saved" not in stored)
    check("скилл побитово не тронут", digest(TARGET), before)

    print("5) новая цель: сливать не с чем")
    fresh = api("plan", "--name", "_probe_plan_new", "--cat", "software-development")
    check("черновика нет → отказ с подсказкой", (fresh.get("ok"), "hint" in fresh), (False, True))
    shutil.copytree(STAGING, REPO / "staging" / "_probe_plan_new")
    (REPO / "staging" / "_probe_plan_new" / "merge-plan.json").unlink(missing_ok=True)
    empty = api("plan", "--name", "_probe_plan_new", "--cat", "software-development")
    check("mode auto без цели", empty.get("mode"), "create")
    check("ни одного слияния", empty["counts"]["merge"], 0)
    check("все файлы новые", empty["counts"]["add"], 4)

    shutil.rmtree(REPO / "staging" / "_probe_plan_new", ignore_errors=True)

    print()
    if failures:
        print(f"ПРОВАЛ: {len(failures)} проверок — {failures}")
        return 1
    print("ВСЁ ЗЕЛЁНОЕ: план по главам работает, скилл не тронут")
    return 0


if __name__ == "__main__":
    sys.exit(main())
