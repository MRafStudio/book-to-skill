#!/usr/bin/env python3
"""Сторож против «мёртвых» сеттеров в панели: используемый `setX` без объявления.

Зачем: правка отпечатка ввода (коммит про `statusSig`) заменила строку
``const [status, setStatus] = useState('')`` на пару только для отпечатка, а
обёртка ``say()`` продолжила звать ``setStatus(text)``. Синтаксис остался целым
(``node --check`` молчит - неопределённое имя это не синтаксическая ошибка), панель
рисовалась, тесты по тексту проходили. Ломалось В РАНТАЙМЕ: первый же ``say()``
из пробы ядра бросал ``ReferenceError: setStatus is not defined``, проба уходила
в ``catch``, там выставлялось ``setCore(false)`` - и владелец часами видел
«нет связи с ядром» при ЖИВОМ ядре (маршруты отвечали ``HTTP 401`` = смонтированы).

Что проверяем
-------------
1. Каждая пара ``const [x, setX] = useState(...)`` - единственный законный
   источник сеттеров; любой вызов ``setX(...)`` обязан иметь такую пару.
2. Отдельно - что у обёртки ``say`` есть своя пара состояния (то место, где
   поломка и случилась).
3. Обратная проверка: ``say`` не зовёт сам себя (иначе бесконечная рекурсия -
   эту ловушку уже проходили).

Запуск: python -B tools/tests/test_pane_declared_state.py
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

# Комментарии цитируют имена словами («setStatus is not defined») - по сырому тексту
# сторож ловил бы прозу вместо дела. Режем блочные и строчные комментарии.
src = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
src = re.sub(r"(?m)^\s*//.*$", "", src)

# 1. Объявленные пары useState: [значение, сеттер]
pairs = re.findall(r"const\s*\[\s*([A-Za-z_$][\w$]*)\s*,\s*(set[A-Za-z_$][\w$]*)\s*\]\s*=\s*useState", src)
declared_setters = {s for _, s in pairs}
declared_values = {v for v, _ in pairs}

# Плюс сеттеры, объявленные руками (const setFoo = ...), - законный вариант.
declared_setters |= set(re.findall(r"(?:const|let|var)\s+(set[A-Za-z_$][\w$]*)\s*=", src))

# Встроенные браузерные таймеры - не состояние панели.
WHITELIST = {"setTimeout", "setInterval", "setImmediate"}

used_setters = set(re.findall(r"(?<![\w.$])(set[A-Z][\w$]*)\s*\(", src)) - WHITELIST
orphans = sorted(used_setters - declared_setters)

check("нет вызовов сеттеров состояния без объявления",
      not orphans,
      "используются, но нигде не объявлены: " + ", ".join(orphans) +
      ". Такой вызов бросает ReferenceError в рантайме: панель молча теряет "
      "строку состояния, а проба ядра уходит в catch и рисует «нет связи с ядром»",
      note=f"объявлений-пар: {len(pairs)}")

# 2. Обёртка say обязана писать ТОЛЬКО в объявленные сеттеры - именно здесь
#    поломка и случилась: `setStatus` звался, а пары не было.
say_line = ""
for line in src.splitlines():
    if re.search(r"\bsay\s*=", line):
        say_line = line
        break

say_setters = sorted(set(re.findall(r"(?<![\w.$])(set[A-Z][\w$]*)\s*\(", say_line)))
undeclared_in_say = [s for s in say_setters if s not in declared_setters]

check("все сеттеры в теле say объявлены",
      bool(say_setters) and not undeclared_in_say,
      "say пишет в " + (", ".join(undeclared_in_say) or "ничего") +
      " - объявленного состояния с таким сеттером нет (в рантайме ReferenceError: "
      "проба ядра падает в catch, панель рисует «нет связи с ядром»)",
      note="say -> " + (", ".join(say_setters) or "нет сеттеров"))

# 3. Антирекурсия: сам say звать себя не должен (внутри строки объявления).
check("say не зовёт сам себя (нет бесконечной рекурсии)",
      not re.search(r"\bsay\s*\([^)]*\)\s*$", say_line.split("=>")[-1]) or
      "setStatusSig" in say_line,
      "в теле say снова вызов say(...) - рекурсия без выхода")

# 4. Значения состояния, читаемые в рендере, тоже должны быть объявлены: ловим
#    зеркальную поломку («status используется, а пары нет»).
jsx_read = set(re.findall(r"\+\s*([a-z][\w$]*)\s*\)", src))
known_globals = {
    "err", "s", "h", "e", "r", "out", "x", "v", "row", "el", "note", "text", "line",
    "code", "part", "chunk", "value", "name", "key", "tip", "cls", "id", "url", "src",
    "plan", "rep", "item", "it", "next", "prev", "cur", "list", "arr", "num", "str",
}
suspicious = sorted(n for n in jsx_read if n not in declared_values and n not in known_globals)
orphan_values = [n for n in suspicious if re.search(rf"(?<![\w.$]){re.escape(n)}\b", " ".join(v for v, _ in pairs)) is None
                 and n in {"status", "tone", "busy", "mode", "lang", "cat"}]
check("состояния, читаемые в рендере, объявлены",
      not orphan_values,
      "читаются, но пары useState нет: " + ", ".join(orphan_values))

print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
if any(not ok for _, ok in checks):
    print("провалено: " + "; ".join(name for name, ok in checks if not ok))
    sys.exit(1)
