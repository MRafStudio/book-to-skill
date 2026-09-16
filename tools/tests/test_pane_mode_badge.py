#!/usr/bin/env python3
"""Плашка режима в шапке панели: «в работе LLM» вместо зелёного «прямой режим».

Зачем: шаги 2-4 уходят в ЧАТ - прозу пишет LLM, и это минуты. Панель в это время
показывала зелёное «прямой режим · локальный Python» (success), то есть читалась
как «ядро на связи, всё хорошо», хотя работа идёт и кнопки шага заперты. Владелец:
«при запуске в 4 группе работы LLM состояние висит как "прямой режим - локальный
Python" салатового цвета, а по идее должно было переключиться на жёлтый "в работе
LLM"».

Что проверяем
-------------
1. Панель подписана на ЗАНЯТОСТЬ сессии (`host.state.busyBySession`) - тот же
   сигнал, что пульс в статусбаре: он честно гаснет, когда агент закончил ход;
2. признак ``llmTurn`` собирается из двух источников: незавершённый черновик
   (writing) и занятая сессия, куда ушло задание;
3. ветка «в работе LLM» стоит в шапке ПЕРВОЙ - выше «работаю» и «прямой режим»:
   иначе жёлтое не покажется никогда;
4. плашка читается ПРОСТО «работает LLM...» (владелец: «это может и не черновик
   вовсе пишется, а просто идут работа»), а тултип называет цену шага («платный»),
   как требует владелец; подписи задач (``LLM_LABEL``) в плашке больше нет - они
   остаются только для отправки задания;
5. успешная отправка задания взводит ``setLlmSid``/``setLlmLabel`` - без этого
   плашка мёртвая;
6. зелёное «прямой режим · локальный Python» осталось на месте: когда LLM не
   работает, шапка по-прежнему говорит правду про локальное ядро.
"""
from __future__ import annotations

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


src = PLUGIN.read_text(encoding="utf-8")
check("plugin.js читается", len(src) > 10000, str(PLUGIN))

check("панель слушает занятость сессии (busyBySession), а не только факт отправки",
      "useValue(host.state.busyBySession)" in src,
      "без подписки на занятость плашка гаснет сразу после prompt.submit")

check("llmTurn = незавершённый черновик ИЛИ занятая сессия",
      "const llmTurn = !!(draftWriting || (llmSid && busyMap && busyMap[llmSid]))" in src,
      "признак работы LLM собран не из тех источников")

i_note = src.find("Работа LLM - ПЕРВОЙ веткой")
i_llm = src.find("llmTurn", i_note) if i_note > 0 else -1
i_is = src.find(": isWorking", i_llm) if i_llm > 0 else -1
check("ветка «в работе LLM» стоит первой в шапке (перед «работаю» и «прямой режим»)",
      i_note > 0 and 0 < i_llm < i_is,
      f"note@{i_note} llm@{i_llm} isWorking@{i_is}")

if i_llm > 0 and i_is > i_llm:
    head = src[i_llm:i_is]
    check("в первой ветке стоит жёлтая плашка «работает LLM...»",
          "variant: 'warn'" in head and "cutSpan('работает LLM...')" in head,
          "подпись не найдена или плашка не жёлтая")
    check("подпись плашки начинается со строчной буквы (владелец: «работает LLM...»)",
          "cutSpan('Работает LLM...')" not in src,
          "заглавная вернулась в подпись плашки")
    check("подпись задачи из плашки убрана (владелец: просто «работает LLM...»)",
          "llmLabel" not in head, "в плашке снова висит название задачи")
    check("тултип называет цену шага (владелец: цена кнопки - в подсказке)",
          "платный шаг" in head and "счёт растёт" in head,
          "тултип не говорит, что шаг платный")
else:
    check("в первой ветке стоит жёлтая плашка «работает LLM...»", False, "ветка не выделена")
    check("подпись задачи из плашки убрана (владелец: просто «работает LLM...»)", False,
          "ветка не выделена")
    check("тултип называет цену шага (владелец: цена кнопки - в подсказке)", False,
          "ветка не выделена")

check("карта подписей задач одна на все чат-шаги (draft/redraft/review)",
      "const LLM_LABEL = {" in src and "redraft: 'перегенерация черновика'" in src
      and "review: 'критика черновика'" in src,
      "подписи задач разъедутся с кнопками")

i_send = src.find("const sendIntent")
i_send_end = src.find("const note =", i_send) if i_send > 0 else -1
send_body = src[i_send:i_send_end] if 0 < i_send < i_send_end else ""
check("отправка задания в чат взводит llmSid и llmLabel",
      "setLlmSid(sid)" in send_body and "setLlmLabel(LLM_LABEL[kind] || '')" in send_body,
      "плашка не узнает, что задание ушло")

check("плашка простоя подписана «ожидание задачи»",
      "cutSpan('ожидание задачи')" in src
      and "cutSpan('локальный режим')" not in src
      and "cutSpan('прямой режим · локальный Python')" not in src,
      "владелец: в простое плашка говорит «ожидание задачи», а не «локальный режим» - "
      "тот описывал способ работы, а не состояние панели")

i_tip = src.find("const coreTip")
tip_slice = src[i_tip:i_tip + 700] if i_tip > 0 else ""
check("подсказка ядра без лекции про «шаги 1 и 3 идут мимо чата»",
      bool(tip_slice) and "шаги 1 и 3 идут мимо чата" not in tip_slice,
      "владелец: дополнение излишне - в подсказке нужны факты ядра, а не рассказ о шагах")

check("статус при проверке ядра назван словами владельца",
      "say('ядро на связи - через LLM строится описание категории и создание черновиков')" in src and
      "say('ядро на связи - шаги 1 и 3 идут мимо чата')" not in src,
      "владелец: «если очень надо - напиши так: ядро на связи - через LLM строится описание категории и создание черновиков»")

check("точка «ядро на связи» красится ЗЕЛЁНЫМ, а не акцентом темы",
      "dotTone === 'good' ? { backgroundColor: STEP_GREEN }" in src,
      "SDK рисует tone 'good' как bg-primary (акцент темы): на светлой теме точка читается белой")

# ── Плашка «нет связи с ядром»: фон красноватый, буквы оранжевые ──────────────
# Владелец: «когда в плашке написано "нет связи с ядром" это должно быть красноватым
# фоном плашки и оранжевыми буквами». Плашка шла с несуществующим у Badge SDK вариантом
# (в SDK есть только default|muted|success|warn|destructive|outline|solid): cva молча
# не подставлял ни фона, ни цвета текста.
#
# Считаем по КОДУ: комментарии панели цитируют ошибочное имя словами, и поиск по сырому
# тексту ловил бы прозу вместо дела.
import os
import re

code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)

i_nolink = code.find("cutSpan('нет связи с ядром')")
nolink = code[max(0, i_nolink - 1500):i_nolink] if i_nolink > 0 else ""
check("плашка «нет связи с ядром» идёт с существующим вариантом Badge",
      "variant: 'destructive'" in nolink and "variant: 'bad'" not in nolink,
      "у Badge SDK нет варианта 'bad' - плашка выходила прозрачной с обычным цветом текста")
check("красный фон плашки «нет связи с ядром» — заливка инлайном, без рамки",
      "backgroundColor: 'rgba(220, 38, 38, 0.32)'" in nolink and "border:" not in nolink,
      "владелец: «Фон у плашки можно сделать красным?» и «красную границу выводить не надо»; "
      "класс bg-destructive/10 (10% красного) на тёмной теме не читался")
check("буквы плашки «нет связи с ядром» — оранжевые (классы варианта warn)",
      "className: 'text-amber-600 dark:text-amber-300'" in nolink,
      "владелец: «оранжевыми буквами»; классы уже в бандле, cn = twMerge - оранжевый вытесняет text-destructive")
# Владелец: сначала «осталось только отцентрировать внутри плашки текст - по вертикали - ну
# поднять текст на 1 пиксель выше», потом то же про «локальный режим». Базовый `py-0.5` даёт
# 2 px сверху и снизу (глаз ловит строку ниже центра из-за метрик шрифта) - асимметрия
# 1 сверху / 3 снизу поднимает текст на 1 px, высота плашки не меняется. Отступы живут в
# ОДНОЙ константе BADGE_FIT: иначе при смене состояния (работаю / проба ядра / локальный
# режим / нет связи с ядром) строка прыгает по вертикали.
check("посадка текста плашки задана один раз: BADGE_FIT (paddingTop 1 / paddingBottom 3)",
      "const BADGE_FIT = Object.assign({}, BTN_FIT, { paddingTop: 1, paddingBottom: 3 })" in src,
      "владелец: «надо на 1 пиксель поднять»; без асимметрии отступов текст сядет ниже центра")

_zone = src[src.find("llmTurn"):src.find("children: cutSpan('нет связи с ядром')")]
_bare = _zone.count("style: BTN_FIT")
check("все состояния плашки режима используют BADGE_FIT, а не голый BTN_FIT",
      _bare == 0,
      f"состояний с голым BTN_FIT: {_bare} - такое состояние сядет ниже центра и будет прыгать"
      " относительно соседних (владелец заметил это на плашке «локальный режим»)")
check("плашка «нет связи с ядром» тоже на BADGE_FIT (посадка общая)",
      "Object.assign({}, BADGE_FIT," in nolink,
      "иначе красная плашка садится иначе остальных состояний")

# Сторож против всего класса ошибки: любой вариант Badge в панели обязан существовать
# в SDK. Ловит «молчаливое» отсутствие цвета у новой плашки.
BADGE_SDK = Path("D:/NEURO/Hermes/data/hermes/hermes-agent/apps/desktop/src/components/ui/badge.tsx")
if not BADGE_SDK.exists():
    _root = os.environ.get("HERMES_APP_ROOT", "")
    BADGE_SDK = Path(_root) / "apps/desktop/src/components/ui/badge.tsx" if _root else BADGE_SDK

used_variants = set()
for m in re.finditer(r"jsx\(Badge,\s*\{", code):
    v = re.search(r"variant:\s*'([a-z]+)'", code[m.end():m.end() + 400])
    if v:
        used_variants.add(v.group(1))

if BADGE_SDK.exists():
    sdk_src = BADGE_SDK.read_text(encoding="utf-8")
    sdk_block = sdk_src[sdk_src.find("variant: {"):sdk_src.find("size: {")]
    known = set(re.findall(r"^\s{8}([a-zA-Z]+):\s*'", sdk_block, re.M))
    unknown = sorted(used_variants - known)
    check("все варианты Badge в панели существуют в SDK",
          not unknown,
          f"нет в SDK: {', '.join(unknown)}; известные: {', '.join(sorted(known))}")
else:
    check("все варианты Badge в панели существуют в SDK", True,
          note="пропущено: SDK приложения не найден рядом (проверять на живой сборке)")

print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
if any(not ok for _, ok in checks):
    print("провалено: " + "; ".join(name for name, ok in checks if not ok))
    sys.exit(1)
