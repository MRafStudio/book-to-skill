#!/usr/bin/env python3
"""Сторож кнопки «СОЗДАТЬ НОВЫЙ СКИЛЛ» (итог записи в профиль).

Правило владельца: «после того, как в шаге 5 на кнопке выводится "Успешно установлен",
ниже 5 группы и выше строки "REST: ..." должна появиться кнопка "СОЗДАТЬ НОВЫЙ СКИЛЛ":
по клику шаг 5 сворачивается, шаг 1 разворачивается. Если на кнопке "Предпросмотр",
кнопка скрыта».

Значит держим три вещи:
1. кнопка есть и её текст ровно «СОЗДАТЬ НОВЫЙ СКИЛЛ»;
2. показывается по ФАКТУ установки (`installed`), а не по собранному плану (`preview`);
3. клик гасит факт установки и план и разворачивает шаг 1 (`onlyB(1)`).

Плюс место в разметке: между блоком 5 и строкой «REST: ...», иначе владелец её не найдёт.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    print(("  OK   " if ok else "  FAIL ") + name + (f" - {note}" if note else (f" - {detail}" if detail else "")))


raw = PLUGIN.read_text(encoding="utf-8")
check("plugin.js читается", len(raw) > 10000, str(PLUGIN))

LABEL = "СОЗДАТЬ НОВЫЙ СКИЛЛ"
# Именно ПОДПИСЬ кнопки: та же строка встречается в комментарии над веткой, и счёт
# «просто вхождений» валил бы проверку на собственном пояснении.
LABEL_PROP = f"label: '{LABEL}'"
check(f"кнопка объявлена ровно один раз - «{LABEL}»",
      raw.count(LABEL_PROP) == 1,
      f"подписей кнопки: {raw.count(LABEL_PROP)}: дубль кнопки в другом месте разметки",
      note=LABEL)

# Ветка кнопки: от её комментария до строки с REST.
i_mark = raw.index("Итог записи: «СОЗДАТЬ НОВЫЙ СКИЛЛ»")
i_rest = raw.index("REST: /rerun")
i_block5 = raw.index("n: 5,")
branch = raw[i_mark:i_rest]

check("кнопка стоит НИЖЕ 5-й группы и ВЫШЕ строки REST",
      i_block5 < i_mark < i_rest,
      f"порядок нарушен: блок5@{i_block5}, кнопка@{i_mark}, REST@{i_rest}")

# Условие показа - то, что стоит ПЕРЕД `? jsx('div'`: в самой ветке `setPreview(null)`
# есть по делу (гасим план), поэтому искать «preview» по всей ветке нельзя.
i_q = branch.index("? jsx('div'")
cond = branch[:i_q]
check("кнопка показывается по факту установки, а не по собранному плану",
      re.search(r"^\s*installed\s*\? jsx\('div'", branch, re.M) is not None
      and "preview" not in cond,
      "условие показа не `installed` (или оглядывается на preview): владелец просил "
      "показывать её при «Успешно установлен» и прятать при «Предпросмотр»")

click = branch[branch.index("onClick"):] if "onClick" in branch else ""
check("клик сворачивает шаг 5 и разворачивает шаг 1 (onlyB(1))",
      "onlyB(1)" in click,
      "клик не переключает на шаг 1: кнопка есть, а нового скилла не начать")

check("клик гасит факт установки - шаг 5 не врёт про прошлый путь",
      "setInstalled(null)" in click,
      "без сброса шаг 5 покажет «установлен в <старый путь>» на новой работе")

check("клик гасит план записи (setPreview(null))",
      "setPreview(null)" in click,
      "план прежних реквизитов остаётся жить - второй клик запишет не то")

check("тултип называет действие и цену (что останется на диске)",
      "вернёмся к шагу 1" in branch and ("staging и сырьё не чистятся" in branch or "Файлы в профиле остаются" in branch),
      "подсказка не говорит, что будет с уже записанным скиллом и мастерской")

# Кнопка - панельная (NextBtn), а не выдуманный элемент: стиль и обрезка текста общие.
check("кнопка сделана штатным NextBtn панели",
      "jsx(NextBtn, {" in branch,
      "своя кнопка мимо NextBtn: другой размер, другое поведение обрезки текста")

print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
if any(not ok for _, ok in checks):
    print("провалено: " + "; ".join(n for n, ok in checks if not ok))
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ: кнопка «СОЗДАТЬ НОВЫЙ СКИЛЛ» живёт по факту установки и возвращает на шаг 1")
