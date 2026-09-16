#!/usr/bin/env python3
"""Сторож протокола, который панель вкладывает в задание на черновик.

Зачем: черновик скилла пишет АГЕНТ в чате, а панель только отправляет задание
и потом читает staging. Задание раньше состояло из одной выжимки -
`b2s draft | src=… | slug=… | name=… | strat=… | mode=… | lang=… | cat=… | act=auto`.
По ней невозможно понять, что делать: откуда взять очищенный текст, куда положить
главы, чем пометить готовность. Скилла-протокола в профиле агента нет (проверено:
в `skills/` его нет), поэтому задание уходило в пустоту - владелец нажал «Сделать
черновик» и получил «Началась твоя работа в чате… Должен был создаться черновик
скилла!». Протокол теперь едет вместе с заданием: координаты, состав черновика,
маркер готовности, запреты.

Что держим
----------
1. протокол существует и прикладывается ТОЛЬКО к черновику (прочие действия -
   по-прежнему короткая выжимка: агенту там хватает имени и категории);
2. в чат уходит склейка (выжимка + протокол), а не одна выжимка;
3. протокол несёт все опоры: ключ черновика, каталог staging, состав файлов,
   концы строк LF, маркер готовности READY.json, запрет лезть в профиль и в сырьё;
4. координаты - из факта разбора (ядро отдаёт путь сырья в отчёте), а не выдуманы;
5. строка в панели не распухает на всю простыню: в `say` идёт выжимка с пометкой.

Запуск: python -B tools/tests/test_pane_draft_brief.py
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

# Протокол - от объявления draftBrief до выжимки отчёта (следующая функция файла).
i_brief = raw.index("function draftBrief(f)")
i_brief_end = raw.index("/** Строка-выжимка по отчёту прогона", i_brief)
brief = raw[i_brief:i_brief_end]

# Отправка задания: от sendIntent до сброса панели (следующая крупная функция).
# Координаты и отправка задания: draftPathsOf объявлена ВЫШЕ sendIntent, поэтому
# смотрим оба: иначе проверка координат ищет их не там, где они живут.
i_paths = raw.index("const draftPathsOf = () =>")
i_send = raw.index("const sendIntent = async (kind) =>")
i_send_end = raw.index("const resetAfterPurge", i_send)
send = raw[i_paths:i_send_end]

check("протокол черновика объявлен функцией панели",
      "function draftBrief(f)" in raw,
      "нет draftBrief: задание снова станет одной выжимкой без инструкций",
      note="draftBrief")

check("протокол прикладывается ТОЛЬКО к черновику",
      "const intentBody = kind === 'draft'" in send
      and "? intentText + '\\n\\n' + draftBrief(" in send
      and ": intentText" in send,
      "протокол клеится ко всем заданиям или не клеится вовсе: "
      "разбор/установка/описание категории поедут агенту лишним текстом",
      note="kind === 'draft' ? … : intentText")

check("в чат уходит склейка выжимки и протокола",
      "host.request('prompt.submit', { session_id: sid, text: intentBody })" in send,
      "в prompt.submit уходит intentText: протокол не доедет до агента",
      note="text: intentBody")

check("строку в панели не распухло простынёй",
      "' (+ протокол черновика)'" in send,
      "say печатает всё задание целиком: панель превратится в полотно текста")

check("вызов протокола ровно один",
      raw.count("draftBrief(") == 2,
      f"draftBrief встречается {raw.count('draftBrief(')} раз: "
      "второй вызов приклеит протокол туда, где он не нужен")

check("координаты берутся из факта разбора, а не выдуманы",
      "report.source_file" in send and "b2s_fetched" in send and "'staging'" in send,
      "draftPathsOf не выводит каталог черновика из пути сырья: агент получит "
      "задание без координат",
      note="source_file → корень проекта → staging/<слаг>")

check("протокол даёт ключ черновика отдельной строкой",
      "ключ черновика (слаг ИСТОЧНИКА)" in brief,
      "в протоколе не сказано, что каталог зовётся слагом источника: "
      "агент может создать каталог по имени скилла, и панель черновик не найдёт")

check("протокол называет каталог черновика",
      "staging" in brief,
      "нет каталога записи: агент не знает, куда класть главы")

check("протокол перечисляет состав черновика",
      all(k in brief for k in ("SKILL.md", "chapters/chNN", "glossary.md",
                               "metadata.json", "cheatsheet.md")),
      "состав черновика неполный: панель читает SKILL.md, chapters, glossary и "
      "metadata, а без cheatsheet/patterns скилл выходит беднее эталонов")

check("протокол требует LF",
      "LF" in brief,
      "не сказано про концы строк: на Windows в staging уедут CRLF, и копия "
      "разойдётся с профилем",
      note="LF")

check("протокол требует маркер готовности",
      "READY.json" in brief and '"ready": true' in brief,
      "нет требования маркера: панель сочтёт черновик обрывком и не отпирает «ДАЛЕЕ»",
      note="READY.json")

check("протокол запрещает лезть в профиль и в сырьё",
      "не писать скилл в профиль" in brief and "b2s_fetched" in brief,
      "нет запретов: агент может записать скилл в профиль мимо панели или "
      "вычистить сырьё разбора")

check("в протоколе нет жаргона «блок N»",
      "блок" not in brief.lower(),
      "тексты для человека говорят «шаг N»: протокол уедет агенту, а тот "
      "перенесёт жаргон в скилл и в отчёт",
      note="без «блок»")

print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
if any(not ok for _, ok in checks):
    print("провалено: " + "; ".join(name for name, ok in checks if not ok))
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ: задание на черновик несёт протокол, а не одну выжимку")
