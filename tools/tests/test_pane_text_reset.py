#!/usr/bin/env python3
"""Сторож сброса экрана разбора при смене источника.

Живой баг (владелец): «ввёл новый адрес, нажал ДАЛЕЕ, а в "Очищенном тексте источника" -
из старого прогона! Почему текст не сбросился в момент, когда я только изменил источник!».

Правила, которые держит этот файл:
1. смена ввода источника (адрес/стратегия/режим) ГАСИТ текст и спойлер сразу, не дожидаясь
   ответа ядра, - иначе до (или вместо) нового разбора человек читает ЧУЖОЙ текст;
2. законный случай сохраняется: отчёт по ЭТОМУ вводу (в том числе восстановленный из
   состояния ядра при переоткрытии панели) текст не гаснет;
3. чтение текста идёт по пути ОТЧЁТА, а не «последний прогон ядра»;
4. если текст не отдан, прежний не остаётся на экране.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    if ok:
        print(f"  OK   {name}" + (f" - {note}" if note else ""))
    else:
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


raw = PLUGIN.read_text(encoding="utf-8")
check("plugin.js читается", len(raw) > 10000, str(PLUGIN))

# 1. Эффект сброса: объявлен по отпечаткам ввода и разбора.
i_eff = raw.find("}, [curSig, fetchedSig])")
check("объявлен эффект сброса экрана разбора на смену ввода",
      i_eff > 0, "нет useEffect с зависимостями [curSig, fetchedSig] - текст прежнего источника "
      "останется на экране")

# Ветка эффекта: от его useEffect до закрывающей зависимости.
i_head = raw.rfind("useEffect(() => {", 0, i_eff) if i_eff > 0 else -1
eff = raw[i_head:i_eff] if i_head >= 0 else ""
check("эффект гасит текст, метрики и раскрытый спойлер",
      "setText('')" in eff and "setTextInfo(null)" in eff and "setOutOpen(false)" in eff,
      "сброс неполный: часть прежнего разбора остаётся на экране")

check("эффект не гасит отчёт по ЭТОМУ вводу (сравнение отпечатков)",
      "fetchedSig !== '' && fetchedSig === curSig" in eff,
      "нет условия совпадения отпечатков: панель будет очищать экран и на своём же источнике "
      "(например, при переоткрытии панели, когда отчёт восстановлен из состояния ядра)")

# 2. Текст читается по пути отчёта, а не «последний прогон ядра».
check("чтение текста принимает путь источника",
      "const loadText = async (limit = 6000, path = '')" in raw,
      "loadText без пути: ядро вернёт текст последнего прогона")

check("путь уезжает в ядро отдельным полем",
      "body: path ? { path, limit } : { limit }" in raw,
      "path не передаётся в /text - окно снова покажет чужой текст")

# 3. Все вызовы чтения текста передают путь отчёта.
calls = re.findall(r"loadText\((.*?)\)", raw)
path_calls = [c for c in calls if "source_file" in c]
check("каждый вызов чтения текста берёт путь отчёта",
      len(calls) == len(path_calls) and len(calls) >= 3,
      f"вызовов {len(calls)}, с путём {len(path_calls)}: без пути остаётся чтение «последнего прогона»",
      note=f"вызовов с путём: {len(path_calls)}")

# 4. Провал чтения не оставляет прежний текст.
i_load = raw.find("const loadText = async")
i_load_end = raw.find("const loadDraft = async", i_load)
load = raw[i_load:i_load_end] if 0 < i_load < i_load_end else ""
else_branch = load[load.find("} else {"):load.find("} catch (err) {")] if "} else {" in load else ""
catch_branch = load[load.find("} catch (err) {"):load.find("} finally {")] if "} catch (err) {" in load else ""
check("при отказе ядра прежний текст убирается (ветка ошибки)",
      "setText('')" in else_branch and "setText('')" in catch_branch,
      "текст не гасится при провале чтения: на экране остаётся чужой текст с подписью об ошибке")

print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
if any(not ok for _, ok in checks):
    print("провалено: " + "; ".join(name for name, ok in checks if not ok))
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ: смена источника гасит экран разбора, а текст читается по своему отчёту")
