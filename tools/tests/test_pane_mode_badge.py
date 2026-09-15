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
4. плашка читается ПРОСТО «Работает LLM...» (владелец: «это может и не черновик
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
    check("в первой ветке стоит жёлтая плашка «Работает LLM...»",
          "variant: 'warn'" in head and "cutSpan('Работает LLM...')" in head,
          "подпись не найдена или плашка не жёлтая")
    check("подпись задачи из плашки убрана (владелец: просто «Работает LLM...»)",
          "llmLabel" not in head, "в плашке снова висит название задачи")
    check("тултип называет цену шага (владелец: цена кнопки - в подсказке)",
          "платный шаг" in head and "счёт растёт" in head,
          "тултип не говорит, что шаг платный")
else:
    check("в первой ветке стоит жёлтая плашка «Работает LLM...»", False, "ветка не выделена")
    check("подпись задачи из плашки убрана (владелец: просто «Работает LLM...»)", False,
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

check("плашка локального режима подписана коротко - «локальный режим»",
      "cutSpan('локальный режим')" in src and "cutSpan('прямой режим · локальный Python')" not in src,
      "владелец: без хвоста «прямой режим - локальный Python»")

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

print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
if any(not ok for _, ok in checks):
    print("провалено: " + "; ".join(name for name, ok in checks if not ok))
    sys.exit(1)
