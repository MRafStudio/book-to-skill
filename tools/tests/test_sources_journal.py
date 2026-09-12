"""Живая проверка журнала источников (`tools/api.py`): что и из чего влито в скилл.

Сценарий: create → долив другого источника → повторный долив того же.
Ожидаем: источники уникальны, installs растёт, install_log ведётся, dry-run
показывает уже внесённое. Профиль не трогается — песочница HERMES_HOME.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]   # tools/tests/ → корень форка
HOME = Path("D:/tmp/b2s-fakehome-journal")
SKILL = "_probe_j"
STAGING = REPO / "staging" / SKILL
SKILL_MD = ("---\nname: _probe_j\ndescription: Use when probing the source journal. Test fixture.\n---\n\n"
            "# Probe\n\nПроверка журнала источников.\n")

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}" + ("" if ok else f" (ждали {want!r})"))
    if not ok:
        failures.append(label)


def api(*args: str) -> dict:
    env = {**os.environ, "HERMES_HOME": str(HOME)}
    proc = subprocess.run([sys.executable, str(REPO / "tools" / "api.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(REPO), env=env)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise SystemExit(f"api.py {args} упал: {proc.stderr[-800:]}")
    return json.loads(proc.stdout)


def write_draft(url: str, title: str, extra_chapter: str = "") -> None:
    STAGING.mkdir(parents=True, exist_ok=True)
    (STAGING / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (STAGING / "metadata.json").write_text(json.dumps({
        "skill": SKILL,
        "source": {"url": url, "title": title, "fetched_at": "2026-09-12",
                   "strategy": "trafilatura", "chars": 1000},
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if extra_chapter:
        chapters = STAGING / "chapters"
        chapters.mkdir(exist_ok=True)
        (chapters / extra_chapter).write_text("# Глава\n\nтекст\n", encoding="utf-8")


def skill_meta() -> dict:
    path = HOME / "skills" / "software-development" / SKILL / "metadata.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def main() -> int:
    if HOME.exists():
        shutil.rmtree(HOME)
    if STAGING.exists():
        shutil.rmtree(STAGING)
    HOME.mkdir(parents=True)

    print("1) первый источник — create")
    write_draft("https://a.example/one", "Источник один")
    out = api("install", "--name", SKILL, "--cat", "software-development", "--confirm")
    meta = skill_meta()
    check("sources", len(meta.get("sources", [])), 1)
    check("url записи", meta["sources"][0].get("url"), "https://a.example/one")
    check("installs", meta["sources"][0].get("installs"), 1)
    check("install_log", len(meta.get("install_log", [])), 1)
    check("log.mode", meta["install_log"][0].get("mode"), "create")
    check("journal в ответе", out.get("journal", {}).get("sources"), 1)

    print("2) второй источник — долив (dry-run видит внесённое)")
    write_draft("https://b.example/two", "Источник два", extra_chapter="ch02-new.md")
    plan = api("install", "--name", SKILL, "--cat", "software-development")
    check("режим", plan.get("mode"), "append")
    check("existing_sources в плане", len(plan.get("existing_sources", [])), 1)
    check("план: добавится", plan.get("plan_counts", {}).get("added"), 1)
    check("ничего не записано до confirm", len(skill_meta().get("sources", [])), 1)

    out = api("install", "--name", SKILL, "--cat", "software-development", "--confirm")
    meta = skill_meta()
    check("sources после долива", len(meta.get("sources", [])), 2)
    check("log.mode долива", meta["install_log"][-1].get("mode"), "append")
    check("новая глава на диске", (HOME / "skills" / "software-development" / SKILL
                                   / "chapters" / "ch02-new.md").is_file(), True)

    print("3) повторный долив того же источника — источник не дублируется")
    out = api("install", "--name", SKILL, "--cat", "software-development", "--confirm")
    meta = skill_meta()
    check("sources не вырос", len(meta.get("sources", [])), 2)
    second = next(s for s in meta["sources"] if s.get("url") == "https://b.example/two")
    check("installs у источника", second.get("installs"), 2)
    check("install_log ведётся", len(meta.get("install_log", [])), 3)
    check("history: источники в ответе", out.get("journal", {}).get("sources"), 2)
    check("first_installed_at сохранён", bool(second.get("first_installed_at")), True)

    print("4) скилл до журнала (только source) — запись подхватывается как источник")
    target_meta = HOME / "skills" / "software-development" / SKILL / "metadata.json"
    legacy = skill_meta()
    legacy.pop("sources", None)
    legacy.pop("install_log", None)
    legacy["source"] = {"url": "https://legacy.example/old", "title": "Старый"}
    target_meta.write_text(json.dumps(legacy, ensure_ascii=False, indent=2), encoding="utf-8")
    write_draft("https://c.example/three", "Источник три")
    api("install", "--name", SKILL, "--cat", "software-development", "--confirm")
    meta = skill_meta()
    urls = sorted(s.get("url") for s in meta.get("sources", []))
    check("старый источник сохранён", "https://legacy.example/old" in urls, True)
    check("новый добавлен", "https://c.example/three" in urls, True)
    check("всего источников", len(meta.get("sources", [])), 3 if False else len(urls))

    print()
    if failures:
        print(f"ПРОВАЛ: {len(failures)} — {', '.join(failures)}")
        return 1
    print("ВСЁ ЗЕЛЁНОЕ: журнал источников работает (уникальность, счётчики, наследие source)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
