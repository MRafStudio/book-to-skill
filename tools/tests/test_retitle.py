#!/usr/bin/env python3
"""Имя скилла при установке: `name:` в шапке SKILL.md приводится к имени каталога.

Зачем: имя живёт в блоке ЗАПИСИ и выбирается уже ПОСЛЕ генерации черновика, а
черновик пишет LLM. Если шапку не поправить, каталог `skills/<категория>/<имя>/`
разъедется с `name:` внутри, и Hermes зовёт скилл чужим именем.

Что проверяем
-------------
1. `name:` заменяется на имя скилла, остальная шапка и тело не тронуты;
2. шапка без `name:` получает его первой строкой;
3. без frontmatter файл не переписывается (лучше не трогать, чем испортить);
4. нет SKILL.md / пустое имя - «не изменено», без исключений;
5. ПРЕДПРОСМОТР не пишет на диск: `do_install(confirm=False)` оставляет шапку как
   была (иначе первый клик «Предпросмотр» тихо правил бы staging);
6. подтверждённая установка кладёт в профиль уже ИСПРАВЛЕННУЮ шапку.

Профиль - временный (`HERMES_HOME` на песочницу), реальные скиллы не читаются.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

SANDBOX = Path(tempfile.mkdtemp(prefix="b2s-retitle-"))
os.environ["HERMES_HOME"] = str(SANDBOX / "hermes")
import api  # noqa: E402

# Каталоги черновиков и сырьё - в песочницу: служебные `_probe*` ядро намеренно
# прячет от поиска (на них стоят тесты), поэтому здесь рабочие имена без подчёркивания,
# но и реальный `_fork/staging` мы не трогаем.
api.STAGING = SANDBOX / "staging"
api.STAGING.mkdir(parents=True, exist_ok=True)
api.FETCH_DIR = SANDBOX / "b2s_fetched"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    print(("  OK   " if ok else "  FAIL ") + name + (f" - {note or detail}" if (note or detail) else ""))


def draft(key: str, body: str) -> Path:
    d = api.STAGING / key
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    (d / "metadata.json").write_text(json.dumps(
        {"source": {"url": "https://example.org/zz", "cleaned_file": key + ".md"}},
        ensure_ascii=False), encoding="utf-8")
    return d


HEAD = ("---\n"
        "name: черновое-имя\n"
        "description: Use when probing the retitle path.\n"
        "version: 1.0.0\n"
        "---\n\n"
        "# Тело\n\n"
        "Проза главы, которую трогать нельзя.\n")

# 1. Замена name: в шапке
d = draft("zz-retitle", HEAD)
res = api._retitle_skill_md(d, "python-pathlib")
text = (d / "SKILL.md").read_text(encoding="utf-8")
check("name: заменён на имя скилла", res == {"changed": True, "name": "python-pathlib"} and "name: python-pathlib" in text)
check("остальная шапка и тело не тронуты",
      "description: Use when probing the retitle path." in text
      and "version: 1.0.0" in text
      and "Проза главы, которую трогать нельзя." in text)
check("чужого name: в теле не появилось", text.count("name:") == 1)

# 2. Шапка без name:
d = draft("zz-retitle2", "---\ndescription: Use when probing.\n---\n\n# Тело\n")
api._retitle_skill_md(d, "b2s-panel")
text = (d / "SKILL.md").read_text(encoding="utf-8")
check("шапка без name: получает его первой строкой",
      text.startswith("---\nname: b2s-panel\ndescription: Use when probing."))

# 3. Без frontmatter - не трогаем
d = draft("zz-retitle3", "# Просто текст\n\nБез шапки вовсе.\n")
res = api._retitle_skill_md(d, "нечто")
check("файл без frontmatter не переписывается",
      res["changed"] is False and (d / "SKILL.md").read_text(encoding="utf-8") == "# Просто текст\n\nБез шапки вовсе.\n")

# 4. Нет файла / пустое имя
d = api.STAGING / "zz-retitle4"
d.mkdir(parents=True, exist_ok=True)
check("нет SKILL.md - «не изменено», без исключения", api._retitle_skill_md(d, "x")["changed"] is False)
d2 = draft("zz-retitle5", HEAD)
check("пустое имя - «не изменено»", api._retitle_skill_md(d2, "")["changed"] is False)

# 5. Предпросмотр не пишет на диск
d = draft("zz-retitle-6", HEAD)
dry = api.do_install("zz-retitle-6", "zz-probe", confirm=False)
check("предпросмотр отвечает dry_run", dry.get("dry_run") is True and dry.get("ok") is True,
      json.dumps({k: dry.get(k) for k in ("ok", "dry_run", "matched")}, ensure_ascii=False))
check("предпросмотр НЕ правит шапку в staging",
      "name: черновое-имя" in (d / "SKILL.md").read_text(encoding="utf-8"))

# 6. Подтверждённая установка пишет исправленную шапку
real = api.do_install("zz-retitle-6", "zz-probe", confirm=True)
target_md = Path(real["target"]) / "SKILL.md"
check("установка отвечает ok", real.get("ok") is True and real.get("installed"), json.dumps(
    {k: real.get(k) for k in ("ok", "installed", "mode")}, ensure_ascii=False))
check("в профиль уехала ИСПРАВЛЕННАЯ шапка",
      target_md.is_file() and "name: zz-retitle-6" in target_md.read_text(encoding="utf-8"),
      str(target_md))
check("ядро отчиталось о правке шапки", (real.get("retitled") or {}).get("changed") is True)
check("черновик помечен как установленный",
      (real.get("installed_mark") or {}).get("skill") == "zz-retitle-6")
check("архивный черновик помечен установленным в своём metadata.json",
      (json.loads((d / "metadata.json").read_text(encoding="utf-8")).get("installed") or {}).get("skill") == "zz-retitle-6")

# Уборка за собой: песочницы и поставленный скилл
for key in ("zz-retitle", "zz-retitle2", "zz-retitle3", "zz-retitle4",
            "zz-retitle5", "zz-retitle6", "zz-retitle-6"):
    p = api.STAGING / key
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
shutil.rmtree(api.STAGING.parent / "skills", ignore_errors=True)
shutil.rmtree(SANDBOX, ignore_errors=True)

if not all(ok for _, ok in checks):
    print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
    sys.exit(1)
print(f"\nпроверок: {len(checks)}, провалов: 0")
print("ВСЁ ЗЕЛЁНОЕ: имя из блока записи доезжает до шапки скилла")
