#!/usr/bin/env python3
"""Живая проверка уборки рабочего каталога (`tools/api.py`: do_drafts/drop/prune).

Зачем: черновик живёт каталогом `staging/<слаг>`, сырьё - файлами
`b2s_fetched/<слаг>.*`, и до сих пор НИ ТО, НИ ДРУГОЕ никто не убирал: после
установки каталог оставался второй копией скилла, а сырьё копилось молча.
Правила (выбраны владельцем):

* каталог по-прежнему принадлежит ИСТОЧНИКУ (слаг), а не имени скилла;
* установленный черновик - архив с TTL, а не мусор: он уходит по сроку;
* свежие держим лимитом (как бэкапы скиллов: BACKUP_KEEP = 5);
* служебные `_probe*` не трогаем никогда - на них стоят тесты ядра;
* молчаливых удалений нет: ядро сначала НАЗЫВАЕТ, что уйдёт (план), и лишь
  с `apply=True` трогает диск.

Безопасность теста: `api.STAGING` и `api.FETCH_DIR` подменяются на временную
песочницу, поэтому реальные черновики в `_fork/staging` не читаются и не удаляются.

Что проверяем
-------------
1. `do_drafts` видит каталоги, но не служебные `_probe*`; активный источник не
   помечается на удаление; установленный по TTL - помечается, с причиной;
2. `do_prune_staging` без `apply` - только план, диск не тронут;
3. `do_prune_staging(apply=True)` убирает переросший TTL черновик ВМЕСТЕ с его
   сырьём, а свежие и установленные-недавно остаются;
4. лимит: сверх `keep` уходят самые старые НЕустановленные, свежие остаются;
5. `do_drop_draft` убирает каталог и сырьё, а служебный `_probe*` отказывается
   убирать; без ключа и без источника - честная ошибка, а не удаление «чего-нибудь»;
6. `do_state()` отдаёт список черновиков (панель берёт его для строки «прежний
   источник»), не падая на пустом профиле.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # tools/tests/ → корень форка
sys.path.insert(0, str(REPO / "tools"))
os.environ["HERMES_HOME"] = "D:/tmp/b2s-fakehome-clean"
import api  # noqa: E402

SANDBOX = Path("D:/tmp/b2s-clean-sandbox")
STAGE = SANDBOX / "staging"
FETCH = SANDBOX / "b2s_fetched"

URL_A = "https://example.org/aaa"
URL_B = "https://example.org/bbb"
URL_C = "https://example.org/ccc"
URL_D = "https://example.org/ddd"
URL_E = "https://example.org/eee"

failures: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: {got!r}" + ("" if ok else f" (ждали {want!r})"))
    if not ok:
        failures.append(label)


def ok(label: str, cond: bool, note: str = "") -> None:
    print(f"  [{'ok ' if cond else 'FAIL'}] {label}{' - ' + note if note else ''}")
    if not cond:
        failures.append(label)


def _stamp(days_ago: float) -> float:
    return time.time() - days_ago * 86400.0


def make_draft(key: str, src: str = "", installed_days: float | None = None,
               age_days: float = 0.0, with_fetch: bool = False) -> Path:
    """Каталог черновика с SKILL.md и, если просят, сырьём в b2s_fetched."""
    d = STAGE / key
    (d / "chapters").mkdir(parents=True, exist_ok=True)
    files = [d / "SKILL.md", d / "chapters" / "ch01.md"]
    files[0].write_text("---\nname: probe\ndescription: fixture\n---\n\n# Probe\n", encoding="utf-8")
    files[1].write_text("# Глава\n\nТекст главы.\n", encoding="utf-8")
    meta: dict = {}
    if src:
        meta["source"] = {"url": src, "cleaned_file": api.slug_for_url(src) + ".md"}
    if installed_days is not None:
        meta["installed"] = {"skill": key, "cat": "probe", "mode": "create",
                             "target": f"skills/probe/{key}",
                             "at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(_stamp(installed_days)))}
    if meta:
        meta_file = d / "metadata.json"
        meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        files.append(meta_file)
    when = _stamp(age_days)
    for f in files:
        os.utime(f, (when, when))
    os.utime(d, (when, when))
    if with_fetch and src:
        FETCH.mkdir(parents=True, exist_ok=True)
        slug = api.slug_for_url(src)
        (FETCH / f"{slug}.md").write_text("сырьё\n", encoding="utf-8")
        (FETCH / f"{slug}.report.json").write_text("{}", encoding="utf-8")
    return d


def reset() -> None:
    """Песочница заново, с одинаковым набором черновиков."""
    shutil.rmtree(SANDBOX, ignore_errors=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    FETCH.mkdir(parents=True, exist_ok=True)
    make_draft("aa-fresh", URL_A, age_days=0.2, with_fetch=True)
    make_draft("bb-fresh", URL_B, age_days=1.0, with_fetch=True)
    make_draft("cc-old", URL_C, age_days=10.0)
    make_draft("dd-inst", URL_D, installed_days=3.0)
    make_draft("ee-inst-old", URL_E, installed_days=10.0, with_fetch=True)
    make_draft("_probe_hidden", URL_A, age_days=0.1)      # служебный: уборке не виден


# Ядро смотрит на СВОИ переменные: подменяем их на песочницу, чтобы уборка не
# касалась реальных черновиков в _fork/staging.
api.STAGING = STAGE
api.FETCH_DIR = FETCH

print("\n=== 1. do_drafts: кто виден и что помечено на удаление")
reset()
info = api.do_drafts(URL_A)
keys = [d["key"] for d in info["dirs"]]
ok("служебный _probe* не виден уборке", "_probe_hidden" not in keys, str(keys))
check("видны ровно пять рабочих каталогов", sorted(keys),
      sorted(["aa-fresh", "bb-fresh", "cc-old", "dd-inst", "ee-inst-old"]))
by = {d["key"]: d for d in info["dirs"]}
ok("активный источник не помечен на удаление", by["aa-fresh"]["active"] and not by["aa-fresh"]["drop"])
ok("установленный по TTL помечен", by["ee-inst-old"]["drop"], by["ee-inst-old"]["reason"])
ok("установленный недавно не тронут", not by["dd-inst"]["drop"])
check("в очереди ровно один", info["droppable"], ["ee-inst-old"])
check("источник каталога прочитан из metadata.json", by["ee-inst-old"]["src"], URL_E)
check("установка названа", by["ee-inst-old"]["installed_skill"], "ee-inst-old")

print("\n=== 2. prune без apply: план есть, диск цел")
plan = api.do_prune_staging(keep=5, ttl_days=7, active_src=URL_A)
check("applied=False", plan["applied"], False)
check("в плане только переросший TTL", [p["key"] for p in plan["plan"]], ["ee-inst-old"])
ok("каталог на диске цел", (STAGE / "ee-inst-old").is_dir())
ok("сырьё на диске цело", (FETCH / (api.slug_for_url(URL_E) + ".md")).is_file())

print("\n=== 3. prune с apply: уходит черновик вместе с сырьём, остальные целы")
applied = api.do_prune_staging(keep=5, ttl_days=7, active_src=URL_A, apply=True)
check("удалён ровно один", applied["dropped"], ["ee-inst-old"])
ok("каталог снесён", not (STAGE / "ee-inst-old").is_dir())
ok("сырьё снесено вместе с ним", not (FETCH / (api.slug_for_url(URL_E) + ".md")).exists())
for key in ("aa-fresh", "bb-fresh", "cc-old", "dd-inst"):
    ok(f"{key} остался", (STAGE / key).is_dir())
ok("служебный _probe* цел", (STAGE / "_probe_hidden").is_dir())

print("\n=== 4. лимит: сверх keep уходят самые старые неустановленные")
reset()
lim = api.do_prune_staging(keep=1, ttl_days=7, active_src=URL_A, apply=True)
dropped = sorted(lim["dropped"])
check("убраны самый старый неустановленный и переросший TTL", dropped,
      ["cc-old", "ee-inst-old"])
ok("активный источник цел", (STAGE / "aa-fresh").is_dir())
ok("свежий неустановленный цел", (STAGE / "bb-fresh").is_dir())
ok("установленный недавно цел", (STAGE / "dd-inst").is_dir())
ok("служебный _probe* не тронут лимитом", (STAGE / "_probe_hidden").is_dir())

print("\n=== 5. drop вручную: каталог + сырьё, и защита служебных")
reset()
one = api.do_drop_draft("bb-fresh", "")
check("каталог убран", one["dropped"], "bb-fresh")
ok("каталога нет", not (STAGE / "bb-fresh").is_dir())
ok("сырьё того же источника убрано", not (FETCH / (api.slug_for_url(URL_B) + ".md")).exists())
guard = api.do_drop_draft("_probe_hidden", "")
check("служебный каталог не убираем", guard["ok"], False)
ok("отказ назван причиной", "тесты ядра" in guard.get("error", ""), guard.get("error", ""))
ok("служебный каталог цел", (STAGE / "_probe_hidden").is_dir())
blind = api.do_drop_draft("", "")
check("без ключа и источника - отказ", blind["ok"], False)
by_src = api.do_drop_draft("", URL_C)
check("по источнику каталог нашёлся и убран", by_src["dropped"], "cc-old")
ok("каталога нет", not (STAGE / "cc-old").is_dir())

print("\n=== 6. do_state отдаёт список черновиков")
state = api.do_state()
ok("поле drafts в состоянии", isinstance(state.get("drafts"), dict))
ok("список каталогов непустой", bool((state.get("drafts") or {}).get("dirs")))

print()
if failures:
    print(f"ИТОГ: ПРОВАЛ - {len(failures)} проверок: {failures}")
    sys.exit(1)
print("ИТОГ: все проверки уборки пройдены")
