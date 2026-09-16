#!/usr/bin/env python3
"""Мастер из четырёх блоков: пропуск анализа для .md и переходы «ДАЛЕЕ».

Зачем блок вообще нужен: панель была простынёй — поля, три кнопки шагов и три
блока результата вперемешку, причём все кнопки жили одновременно, поэтому
двойной клик в один тик отправлял два REST, а сбой ядра тихо уходил в чат
(``sendIntent('rerun')``) — счёт за LLM рос, хотя шаг 1 делает python, а не LLM.
Владелец перевёл это в мастер: четыре сворачиваемых блока (источник → анализ →
черновик → запись), «ДАЛЕЕ» в правом нижнем углу, открыт один блок за раз.

Отдельный случай, который владелец попросил не потерять: **выбранный файл уже
markdown**. Если источник — ``.md``/``.markdown`` (локальный путь или URL, у
которого путь кончается на ``.md``; ``?raw=1`` и ``#anchor`` не мешают), блок 2
пропускается: «ДАЛЕЕ» ведёт сразу в блок 3, а шапка честно подписана «пропущен:
файл уже markdown». Признак берётся из строки, без сети, — решение видно до
нажатия. **``.txt`` markdown НЕ считается** (владелец: «txt всё же требует
отдельного разбора — это далеко не md»): у него нет разметки заголовков, главы
в нём ищет ядро, а значит блок 2 нужен.

Что проверяем
-------------
1. ``srcIsMarkdown``/``isRemoteSrc`` вырезаются из ``plugin.js`` и исполняются в
   node на 15 кейсах (путь, URL, query, якорь, .txt, .html, .pdf, .epub, папка);
2. в дереве стоят ровно пять блоков ``PaneBlock``, у каждого — кнопка ``NextBtn``
   в правом нижнем углу (футер с ``min-w-0 flex-1``: подпись слева, кнопка справа);
3. переходы: ``next1`` (учитывает md), ``next2`` (требует отчёт), ``next3``
   (не пускает без имени скилла), ``next4`` (требует черновик в staging); шапка
   подписана «пропущен: файл уже markdown»;
4. герметизация: ``busyRef`` стоит и в ``runRerun``, и в ``sendIntent`` — второй
   клик в один тик не копит вызовы;
5. тихий фоллбэк снят: ``sendIntent('rerun')`` в живом коде не остался (только в
   пояснительном комментарии), вместо него — ``rerunErr`` + кнопка «Повторить»;
6. провал адресный: панель берёт у ядра ``failure_kind`` и краснит ИМЕННО свой шаг —
   «источник не получен» (шаг 1) не зажигает блок 2, до которого работа не дошла
   (владелец: «просто ввёл в поле "Источник" цифру 1. А получил - группу 2 "Анализ
   источника" с красной границей»);
7. зелёная граница блока 1 — только у РАЗОБРАННОГО источника: ``analyzed`` требует,
   чтобы отчёт был снят с этих же входов И принадлежал этому источнику, а отпечаток
   входов панель берёт у отчёта ядра (``report_inputs``), не у поля ввода — иначе
   голая «1» в поле светилась зелёным «анализ завершён» (владелец: «это что —
   найденный валидный файл на диске, но тогда на каком?»).
8. тексты панели говорят «шаг N», а не «блок N»: жаргон ловится лексером строковых
   литералов (комментарии не трогаем - там «блок» законное имя куска кода, ``openB[3]``).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    if ok:
        print(f"  OK   {name}" + (f" — {note}" if note else ""))
    else:
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


RUNNER = r"""
const jsx = (t, p) => ({ type: t, props: p || {} })
const jsxs = (t, p) => ({ type: t, props: p || {} })
const api = new Function('jsx', 'jsxs', process.env.MD_SRC +
  '\n; return { MD_EXT, stripQuery, isRemoteSrc, srcIsMarkdown, skillsInCat, analysisSigOf }')(jsx, jsxs)
const { isRemoteSrc, srcIsMarkdown, skillsInCat, analysisSigOf } = api
const skillFixtures = [
  { name: 'swift-notes', category: 'apple', chapters: 3, files: 9 },
  { name: 'ios-hig', category: 'apple', chapters: 5, files: 14 },
  { name: 'python-pathlib', category: 'software-development', chapters: 10, files: 15 },
  { name: 'loose-one', category: '', chapters: 1, files: 2 },
  { name: 'k8s', category: 'devops', chapters: 4, files: 11 }
]
const cases = [
  'D:/src/docs/HERMES.md',
  'D:/src/docs/note.markdown',
  'D:/docs/../a.MD',
  'D:\\src\\docs\\deep\\manual.md',
  'https://raw.githubusercontent.com/u/r/main/README.md',
  'https://example.com/page.md?raw=1',
  'https://example.com/page.md#section',
  'https://example.com/README?file=a.md',
  'D:/src/docs/notes.txt',
  'https://docs.python.org/3/library/pathlib.html',
  'D:/src/docs',
  'book.pdf',
  'D:/books/manual.epub',
  'https://example.com/api/v1/items',
  '',
  '   '
]
console.log(JSON.stringify({
  marks: cases.map(srcIsMarkdown),
  n: cases.length,
  remote: [isRemoteSrc('https://a.b/c.md'), isRemoteSrc('D:/a/b.md'), isRemoteSrc(''), isRemoteSrc('  ')],
  ext_upper: srcIsMarkdown('D:/X/Y.MD'),
  clean: srcIsMarkdown('D:/a/b.md'),
  cat_apple: skillsInCat(skillFixtures, 'apple').map((s) => s.name),
  cat_sd: skillsInCat(skillFixtures, 'software-development').map((s) => s.name),
  cat_none: skillsInCat(skillFixtures, 'nope').map((s) => s.name),
  cat_loose: skillsInCat(skillFixtures, '').map((s) => s.name),
  cat_empty: skillsInCat(null, 'apple').length,
  cat_sorted: skillsInCat(skillFixtures, 'apple').map((s) => s.name).join(','),
  sig_same: analysisSigOf({ src: 'u', strat: 'auto', mode: 'technical' }) ===
            analysisSigOf({ src: ' u ', strat: 'auto', mode: 'technical' }),
  sig_src: analysisSigOf({ src: 'u', strat: 'auto', mode: 'technical' }) !==
           analysisSigOf({ src: 'v', strat: 'auto', mode: 'technical' }),
  /* Имя скилла и категория - реквизиты ЗАПИСИ, а не входы разбора: их правка не
     имеет права объявлять живой отчёт устаревшим (владелец: «один чих - и заново
     делай черновик»). */
  sig_name_same: analysisSigOf({ src: 'u', strat: 'auto', mode: 'technical', name: 'a', cat: 'c' }) ===
                 analysisSigOf({ src: 'u', strat: 'auto', mode: 'technical', name: 'b', cat: 'c' }),
  sig_cat_same: analysisSigOf({ src: 'u', strat: 'auto', mode: 'technical', name: 'a', cat: 'c' }) ===
                analysisSigOf({ src: 'u', strat: 'auto', mode: 'technical', name: 'a', cat: 'd' }),
  sig_mode: analysisSigOf({ src: 'u', strat: 'auto', mode: 'technical' }) !==
            analysisSigOf({ src: 'u', strat: 'auto', mode: 'study' }),
  sig_strat: analysisSigOf({ src: 'u', strat: 'auto', mode: 'technical' }) !==
             analysisSigOf({ src: 'u', strat: 'html-cascade', mode: 'technical' }),
  sig_empty: analysisSigOf({}) === analysisSigOf({ src: '', strat: '', mode: '' }),
  sig_glue: analysisSigOf({ src: 'a', strat: 'b', mode: 'c' }) !==
            analysisSigOf({ src: 'a\\u0001b', strat: 'c', mode: 'd' })
}))
"""


def ui_texts(src: str) -> list[str]:
    """Строковые литералы ``plugin.js`` - то, что реально видит человек.

    Нужна, чтобы отличить ТЕКСТ подписи от КОММЕНТАРИЯ. В комментариях «блок N» -
    законное имя куска кода (``openB[3]``, «блок 4 соберёт черновик»), а в текстах
    это жаргон, который владелец читает и не понимает: «правильнее - следующий шаг
    соберёт новый скилл». Обычный грепа по строкам тут врёт - комментарий
    ``// ... «блок 4» ...`` выглядит ровно как текст подписи.
    """
    out: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if quote is None:
            if c == "/" and src.startswith("//", i):
                nl = src.find("\n", i)
                if nl < 0:
                    break
                i = nl
                continue
            if c == "/" and src.startswith("/*", i):
                end = src.find("*/", i + 2)
                i = n if end < 0 else end + 2
                continue
            if c in "\"'":
                quote, i = c, i + 1
                continue
        else:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                out.append("".join(buf))
                buf.clear()
                quote = None
                i += 1
                continue
            buf.append(c)
            i += 1
            continue
        i += 1
    return out


def main() -> int:
    if not PLUGIN.is_file():
        print(f"нет файла плагина: {PLUGIN}")
        return 1
    src = PLUGIN.read_text(encoding="utf-8")

    # 1) живые функции детекта — в node
    try:
        md_src = (src[src.index("const MD_EXT"):src.index("const fmtInt")] +
                  src[src.index("/** Отпечаток ВХОДОВ разбора"):src.index("/** Скиллы ВЫБРАННОЙ категории")] +
                  src[src.index("function skillsInCat"):src.index("/** Заголовок свёрнутого спойлера")])
    except ValueError as exc:
        check("детект markdown извлекается из plugin.js", False, str(exc))
        return 1
    check("детект markdown извлекается из plugin.js", len(md_src) > 300,
          f"длина {len(md_src)}", note=f"{len(md_src)} символов живого кода")

    with tempfile.TemporaryDirectory(prefix="b2s-pane-blocks-") as tmp:
        tmpd = Path(tmp)
        (tmpd / "run.mjs").write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ)
        env["MD_SRC"] = md_src
        proc = subprocess.run(
            ["node", str(tmpd / "run.mjs")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )
    if proc.returncode != 0:
        check("детект исполняется в node", False, (proc.stderr or proc.stdout)[-600:])
        return 1
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    check("детект исполняется в node", True, note="чистая функция, без сети и React")

    marks = out["marks"]
    names = [
        "путь .md", "путь .markdown", "путь .MD (регистр + ..)", "путь Windows .md",
        "URL raw github .md", "URL .md?raw=1", "URL .md#якорь",
        "URL без .md (query упоминает .md)", ".txt — НЕ markdown",
        "html-страница документации", "папка без расширения", "book.pdf", "manual.epub",
        "URL без расширения", "пустая строка", "пробелы",
    ]
    expect = [True, True, True, True, True, True, True, False, False, False, False, False, False, False, False, False]
    wrong = [f"{names[i]}→{marks[i]} (ждали {expect[i]})" for i in range(len(expect)) if marks[i] != expect[i]]
    check(f"детект markdown верен на {out['n']} кейсах (md, txt, html, pdf, epub, папка, пусто)",
          not wrong, "; ".join(wrong),
          note=".txt — отдельный разбор, не markdown")
    check("регистр расширения не важен (.MD)",
          out["ext_upper"] is True, f"{out['ext_upper']!r}")
    check("URL отличается от локального пути (для подписей блока)",
          out["remote"] == [True, False, False, False], f"{out['remote']!r}")

    # 1b) список имён скиллов подчиняется выбранной КАТЕГОРИИ (жалоба владельца:
    # «выбрали категорию apple - значит в списке должны быть только те, кто входит
    # в каталог apple, а не всё, что в принципе найдено на диске»).
    check("список имён подчиняется категории: apple даёт только свои скиллы",
          out["cat_apple"] == ["ios-hig", "swift-notes"], f"{out['cat_apple']!r}")
    check("другая категория даёт другой список (не «всё, что на диске»)",
          out["cat_sd"] == ["python-pathlib"], f"{out['cat_sd']!r}")
    check("пустая категория честно даёт пустой список",
          out["cat_none"] == [], f"{out['cat_none']!r}")
    check("скиллы вне категорий (loose) в список имён не подмешиваются",
          out["cat_loose"] == [], f"{out['cat_loose']!r}")
    check("нет списка скиллов — не падаем", out["cat_empty"] == 0)
    check("имена в списке по алфавиту", out["cat_sorted"] == "ios-hig,swift-notes")
    check("нативный datalist убран из панели (все скиллы подряд + непрокручиваемый попап)",
          "jsx('datalist'" not in src and "b2s-skill-names" not in src,
          "имя скилла всё ещё подсказывается нативным datalist")
    check("список имён — окно панели (фон плагина, рамка, потолок и скролл)",
          "Object.assign({}, ZONE_CAP, { border: FIELD_LINE, backgroundColor: PANEL_BG })" in
          src[src.index("const nameListBlock"):src.index("const namePickRow")])
    check("список открывается кнопкой и перечитывается с диска при открытии",
          "loadSkills(); setNameOpen((v) => !v)" in src)
    check("ввод имени остался свободным (Input не превратился в Select), "
          "а ручная правка помечает ввод: подстановка больше не тронет поле",
          "onChange: (e) => { setName(e.target.value); nameTouched.current = true; setNameAuto(false) }" in src)

    # 1d) «Имя скилла» — обязательный вход, и панель это ПОКАЗЫВАЕТ, а не молчит
    # (владелец: «по умолчанию либо предыдущее, либо пустое, но никак не
    # "python-pathlib"»; «окантовка поля жёлтым, а кнопка ДАЛЕЕ недоступна»).
    check("имя скилла по умолчанию пустое или прошлое, а не чужой пример",
          "useState(stored.name ? String(stored.name).toLowerCase() : '')" in src and
          "stored.name || 'python-pathlib'" not in src,
          "панель подставит своё имя, и скилл уедет под чужим (восстановленное из storage "
          "имя нормализуем - подробности и сторож в test_pane_name.py)")
    check("пустое имя подсвечено: рамка ошибки КРАСНАЯ, подпись под полем оранжевая",
          "const nameWarn = !wanted" in src and
          "color-mix(in srgb, ' + STOP_RED + ' 45%, transparent)" in src and
          "style: (nameWarn || nameBad) ? { color: WARN_YELLOW } : null" in src and
          "text-amber-400" not in src,
          "владелец: рамку ошибки - как тонкую красную у блока 3, а краску текста не трогать")
    check("окантовка имени не жирнее чипсы DESCRIPTION.md: ровно 1 px, без boxShadow",
          "NAME_WARN" not in src and "boxShadow: 'inset 0 0 0 1px '" not in src,
          "borderColor + inset-тень давали двойную линию - владелец: «беспрецедентно толстая»")
    check("пустое имя названо словами, а не только цветом",
          "nameWarn ? 'обязательное: пустым не поставим'" in src)
    check("подпись «такого скилла нет - будет создан новый» (без обрубка «будет новый»)",
          "такого скилла нет - будет создан новый" in src and "будет новый'" not in src)
    check("шапка Field одной высоты - поля не «пляшут» по вертикали",
          "className: 'flex h-4 items-center gap-2'" in src and
          "className: 'flex items-baseline gap-2'" not in src,
          "владелец: «Имя скилла на 1-2 px ниже Категории» - у поля с хинтом шапка выше")
    check("подпись-хинт посажена на HINT_FIT, а не «висит» выше лейбла",
          "const HINT_FIT = {" in src and
          "style: Object.assign({}, CUT, HINT_FIT)" in src,
          "владелец: после лейбла «Имя скилла» подпись «свободно - новый» стояла ВЫШЕ "
          "основной строки - кегль 10 px против 11 px садится выше при items-center")
    check("главная кнопка шага отбита от содержимого (футер с pt-1)",
          "className: 'flex min-w-0 items-center gap-2 px-2 pb-2 pt-1'" in src,
          "владелец: кнопки «ДАЛЕЕ» и «Предпросмотр» надо опустить минимум на 3 px")
    check("«ДАЛЕЕ» блоков 3-5 требует ПРОЙДЕННОГО блока 2",
          "disabled: !!busy || nameWarn || nameBad || !block2Passed" in src and
          "disabled: !!busy || !draftReady || !block2Passed" in src and
          "|| nameWarn || nameBad || !block2Passed," in src,
          "владелец: кнопку нельзя открывать в обход «Анализа источника»")
    check("блок 2 пройден и для markdown-источника (analyzed || mdSrc)",
          "const block2Passed = analyzed || mdSrc" in src,
          "иначе md-источник заклинило бы: разбор ему не нужен, но и пройти его нельзя")
    check("обход закрыт и в обработчиках next3/next4",
          src.count("if (!block2Passed) {") >= 2,
          "кнопки блокируются, а клик по шапке блока открывает его - проверять надо и в переходе")
    check("состояние в шапке блока выровнено по базовой линии заголовка",
          "items-baseline gap-1 rounded-md px-2 py-1" in src and
          "cutSpan(n + '. ' + title, 'text-[0.6875rem] font-medium leading-none')" in src and
          "cutSpan(' · ' + state, 'text-[0.625rem] leading-none opacity-80')" in src,
          "владелец: дополнение через точку писалось на 1-2 px выше заголовка блока")
    check("блок 1 требует ТОЛЬКО источник: имя уехало в блок 3",
          "disabled: !!busy || !trimSrc || (!!srcProbe && !srcProbe.ok)," in src and
          "disabled: !!busy || !trimSrc || nameWarn" not in src,
          "имя снова стало входом разбора")
    # Тексты владельца храним ДОСЛОВНО. Подсказка блока 1 (источник введён, разбора
    # ещё не было) - его фраза; пересказ агента («блок 2 разберёт источник») владелец
    # принимает как ошибку, поэтому сторож ловит и самодельную формулировку.
    check("подсказка блока 1 - дословно фраза владельца",
          "'Следующий шаг проанализирует источник данных'" in src
          and "блок 2 разберёт источник" not in src,
          "владелец: «я просил - Следующий шаг проанализирует источник данных»")
    # Блок 2: подсказка зовёт ВПЕРЁД и когда отчёт снят с этих входов, и когда он устарел
    # (владелец: «нужно звать вперёд»). Состояние отчёта и что нажать называет статус блока.
    check("подсказка блока 2 после разбора - дословно фраза владельца",
          "'Следующим шагом укажи категорию и имя скилла'" in src
          and "(analyzed || staleReport) ? 'Следующим шагом укажи категорию и имя скилла'" in src
          and "а не от этих" not in src,
          "владелец: «Замени на - Следующим шагом укажи категорию и имя скилла» + «нужно звать вперёд»")
    check("пустое имя гасит «ДАЛЕЕ» блока 3 и установку в блоке 5, а не разбор",
          "disabled: !!busy || nameWarn || nameBad || !block2Passed," in src and
          "|| nameWarn || nameBad || !block2Passed," in src and
          "disabled: !!busy || !hasDraft,   // имя" not in src,
          "пустое имя стало входом разбора или потеряло замок")
    check("имя обязательно там, где пишется каталог (блок 5)",
          "|| nameWarn || nameBad || !block2Passed," in src and
          "нужно имя скилла: пустым не поставим - каталог в skills/ должен быть назван" in src,
          "установка пойдёт без имени")
    # Имя, которое линза Hermes не примет, - такая же незаполненная обязательная
    # строка, как пустая, только ошибка вылезала ПОСЛЕ записи: скилл ложился в
    # профиль с шапкой `name: license-Info`, а линза отвечала «must be lowercase».
    check("недопустимое имя (заглавные, пробелы) ловится ДО записи",
          "const nameBad = !nameWarn && !/^[a-z0-9][a-z0-9._-]*$/.test(wanted)" in src and
          "шаг 3 · такое имя Hermes не примет" in src and
          "if (nameBad)" in src,
          "имя с заглавной снова доедет до установки и упадёт на линзе")

    # Тексты панели говорят «шаг N»: «блок N» - внутренний жаргон, который человек
    # читает как чужой код (владелец: «блок 4 соберёт новый скилл» → «следующий шаг
    # соберёт новый скилл»). Комментарии при этом не трогаем - там «блок» законное
    # имя куска кода, поэтому сравниваем только строковые литералы.
    jargon = [t for t in ui_texts(src)
              if re.search(r"блок[а-я]* \d", t) or "в этом блоке" in t]
    check("в текстах панели нет «блок N» - человеку говорим «шаг N»",
          not jargon, f"жаргон: {[t[:60] for t in jargon[:3]]}")
    check("комментарии про внутренние блоки не тронуты",
          src.count("блок") >= 100 and "openB[" in src,
          "замена текстов не должна залезать в комментарии и имена переменных")
    check("подпись недопустимого имени называет правило, а не только красит",
          "только строчные латинские буквы, цифры" in src,
          "владелец видит жёлтое поле без причины")
    # Красная граница блока 3 - про «не хватает обязательного НА ЭТОМ шаге». Пока блок 2
    # не пройден, имя пусто у всех, и краснеть рано: владелец зовёт это вычурным и просит
    # нейтральный блок 3 до прохода блоков 1 и 2. Кнопка «ДАЛЕЕ» остаётся погашенной.
    #    Условие тона расширено правкой «зелёная рамка шага 3»: красный теперь и на
    #    негодном имени (nameBad - незаполненный обязательный вход, просто причина другая),
    #    зелёный - по флагу прохождения (nameSeen). Здесь держим прежнюю суть: до прохода
    #    блока 2 рамка нейтральна и красной не бывает. Сам зелёный сторожит test_pane_step3.py.
    check("красная граница блока 3 появляется только после прохода блока 2",
          "tone: ((nameWarn || nameBad) && block2Passed)" in src
          and "tone: (nameWarn && block2Passed) ? 'bad' : null," not in src
          and "tone: nameWarn ? 'bad' : null," not in src,
          "блок 3 краснеет до того, как пройдены блоки 1 и 2")

    # 1c) отпечаток ВХОДОВ разбора: его меняют только входы РАЗБОРА - источник,
    # стратегия, режим. Имя скилла и категория сюда НЕ входят: это реквизиты записи,
    # и их правка не имеет права обесценивать живой отчёт (владелец: «один чих - и
    # заново делай черновик»). Отчёт про другой набор входов не свежий.
    check("отпечаток входов разбора устойчив к пробелам (u == ' u ')",
          out["sig_same"] is True, f"{out['sig_same']!r}")
    check("смена источника меняет отпечаток (отчёт прошлого прогона не свежий)",
          out["sig_src"] is True, f"{out['sig_src']!r}")
    check("смена имени скилла отпечаток НЕ меняет (имя - реквизит записи)",
          out["sig_name_same"] is True, f"{out['sig_name_same']!r}")
    check("смена режима и стратегии меняет отпечаток",
          out["sig_mode"] is True and out["sig_strat"] is True,
          f"mode={out['sig_mode']!r}, strat={out['sig_strat']!r}")
    check("смена категории отпечаток НЕ меняет (категория - реквизит записи)",
          out["sig_cat_same"] is True, f"{out['sig_cat_same']!r}")
    check("пустые входы дают один и тот же отпечаток (нет «вечного» отчёта)",
          out["sig_empty"] is True, f"{out['sig_empty']!r}")
    check("разделитель не даёт склейки двух наборов в один отпечаток",
          out["sig_glue"] is True, f"{out['sig_glue']!r}")
    check("analyzed требует совпадения отпечатка (fetchedSig === curSig)",
          "fetchedSig !== '' && fetchedSig === curSig" in src and
          "const sig = analysisSigOf({ src, strat, mode })" in src and
          "setFetchedSig(sig)" in src,
          "шаг 2 может пустить к черновику по отчёту от других входов")

    # 1d) черновик принадлежит ИСТОЧНИКУ, а не имени скилла: правка имени или
    # категории не смеет ни терять черновик, ни гасить план блока 4, ни сбрасывать
    # раскладку глав. Ключ черновика - слаг источника, он же едет агенту в интенте.
    check("черновик читается по источнику (src едет в /draft, из ref - не из замыкания)",
          "body: { name: nameRef.current, src: srcRef.current }" in src)
    _src = "src: (src || '').trim()"
    check("план и установка знают источник (src едет в /plan и /install)",
          src.count(_src) >= 4, "вхождений: " + str(src.count(_src)))
    check("вотчер черновика ищет по источнику, а не по имени",
          "watchDraft((name || '').trim(), (src || '').trim())" in src)
    check("ключ черновика (слаг) едет в интенте агенту",
          "push('slug', f.slug)" in src and "slug: draftKey" in src)
    check("правка имени/категории НЕ гасит план блока 4, а пересобирает его",
          "setInstalled(null)" in src and "previewInstall(false) }, 700)" in src)
    check("правка имени/категории НЕ сбрасывает раскладку по главам",
          "setChapterPlan(null) }, [name, cat, act])" not in src)
    check("имя и категория убраны из входа, гасящего план",
          "[src, act, mode, lang, strat]" in src and
          "[src, name, cat, act, mode, lang, strat]" not in src)
    # Состояние «отчёт снят не с этих входов» несёт ТОЛЬКО статус блока 2 («Нажми на
    # «Анализ источника»...»): самого спойлера с чужими метриками в панели больше нет,
    # и метку «от прежних входов» показывать негде - владелец: «берёт данные со старого
    # источника, этого быть не должно».
    check("устаревший отчёт назван словами в статусе блока 2, а не в подсказке",
          "staleReport" in src and "'Нажми на «Анализ источника» чтобы повторить обработку'" in src
          and "а не от этих" not in src)
    # Чужой отчёт не показывается вовсе: спойлер «Результат разбора» рендерится лишь
    # тогда, когда отчёт снят с ТЕКУЩИХ входов (`analyzed`). Раньше он был безусловным,
    # и панель выдавала метрики, путь и текст прежнего разбора за результат текущего.
    check("спойлер «Результат разбора» есть только у отчёта с текущих входов",
          "const resultBlock = !analyzed ? null : jsxs('details', {" in src
          and "' · от прежних входов'" not in src,
          "спойлер снова показывает метрики и текст прежнего разбора")
    # Отпечаток входов восстанавливается из состояния ядра при старте панели: иначе
    # после перезапуска Desktop живой отчёт по текущему источнику прятался бы как чужой.
    # Берём именно ВХОДЫ ОТЧЁТА (`report_inputs`), не «текущий ввод» ядра (`stt.src`):
    # `src` пишется на любом вводе, и после провального прогона «1» в поле блок 1
    # светился зелёным «анализ завершён» (владелец: «на каком файле? куда URL?»).
    check("отпечаток входов разбора восстанавливается из /state по ВХОДАМ ОТЧЁТА",
          "const ri = stt.report_inputs" in src
          and "setFetchedSig(analysisSigOf({ src: ri.src, strat: ri.strat, mode: ri.mode }))" in src
          and "const stt = (s && s.state) || {}" in src,
          "после перезапуска панели свежий отчёт выглядит чужим либо провал сходит за разбор")
    # Отчёт ЧУЖОГО источника = как отсутствие отчёта: панель не зовёт «повторить обработку»
    # и не выдаёт чужие метрики за прежние входы этого шага (staleReport гейтится !reportOtherSrc).
    check("отчёт другого источника не считается прежними входами шага",
          "const staleReport = !!report && !analyzed && !reportOtherSrc" in src
          and "const reportOtherSrc = !!report && !!reportSrc && normSrc(reportSrc) !== normSrc(trimSrc)" in src,
          "панель зовёт «повторить обработку» по отчёту другого источника")
    check("кнопка шага 1 при работе говорит «Идёт разбор…», а не обещает результат",
          "busy === 'rerun' ? 'Идёт разбор…'" in src and "busy === 'rerun' ? '⏳'" in src)

    # 2) пять блоков и футер «ДАЛЕЕ» в каждом
    blocks = re.findall(r"jsx\(PaneBlock, \{\n\s+n: (\d)", src)
    check("в дереве ровно пять блоков мастера (PaneBlock n: 1..5)",
          blocks == ["1", "2", "3", "4", "5"], f"{blocks!r}")
    # Считаем кнопки В ФУТЕРАХ: правило про «своя кнопка в каждом блоке», а панельная NextBtn
    # бывает и вне блока (кнопка «СОЗДАТЬ НОВЫЙ СКИЛЛ» под шагом 5) - по общему счёту она
    # давала шестую и валила проверку.
    check("у каждого блока своя кнопка в футере (NextBtn)",
          src.count("foot: jsx(NextBtn, {") == 5 and src.count("jsx(NextBtn, {") >= 5,
          f"в футерах: {src.count('foot: jsx(NextBtn, {')}, всего вызовов: {src.count('jsx(NextBtn, {')}")
    check("футер блока: подпись слева, кнопка справа (min-w-0 flex-1 перед foot)",
          src.count("min-w-0 flex-1") >= 1 and "justify-end" in src,
          "нет растяжки подписи перед кнопкой")
    check("каждый блок сворачивается кликом по шапке (каркас aria-expanded + onToggle ×5)",
          "'aria-expanded': open ? 'true' : 'false'" in src and
          src.count("onToggle: () => toggleB(") == 5,
          f"onToggle={src.count('onToggle: () => toggleB(')}")

    # 3) переходы
    check("переход из блока 1 учитывает готовый markdown (next1 → толькоB(3))",
          "const next1 = async () => {" in src and "onlyB(3)" in src,
          "нет ветки «сразу в блок 3»")
    check("переход из блока 2 требует отчёт о разборе (analyzed)",
          "const next2 = () => {" in src and "if (!analyzed)" in src,
          "«ДАЛЕЕ» блока 2 не проверяет разбор")
    # Пропущенный шаг - это ПРОЙДЕННЫЙ шаг: панель сама пишет «блок 2 пропустим» у
    # markdown-источника, и оставлять его серым нельзя (владелец: «раз пропустим, то
    # почему тогда блок 2 не окрасился зелёным? Ведь напрашивается?»).
    check("у markdown-источника блок 2 ЗЕЛЁНЫЙ (пройден), а не серый",
          "tone: mdSrc\n          ? 'done'" in src,
          "пропущенный шаг выглядел как нетронутый и вводил в заблуждение")
    check("«ДАЛЕЕ» блока 2 не гаснет у markdown-источника (шаг пройден)",
          "disabled: !!busy || !block2Passed," in src
          and "файл уже markdown - разбор не нужен: открыть следующий шаг" in src,
          "кнопка гасла по !analyzed - мастер вёл в тупик при валидном источнике")
    check("переход из блока 2 у markdown-источника идёт тем же путём, что из блока 1",
          "if (mdSrc && !analyzed) { next1(); return }" in src,
          "зелёный шаг с кнопкой, которая не ведёт дальше")
    check("переход из блока 3 не пускает без имени скилла (nameWarn)",
          "const next3 = () => {" in src and "if (nameWarn)" in src and
          "каталог установки называется именем" in src,
          "«ДАЛЕЕ» блока 3 пускает к черновику без имени")
    check("переход из блока 4 требует ГОТОВЫЙ черновик (маркер от LLM)",
              "const next4 = () => {" in src and "if (!hasDraft)" in src
              and "if (!draftReady)" in src and "маркер готовности" in src,
              "«ДАЛЕЕ» блока 4 пускает на пишущемся черновике")
    check("шапка пропущенного блока 2 подписана «пропущен: файл уже markdown»",
          "пропущен: файл уже markdown" in src)
    check("открытым может быть только один блок (onlyB сбрасывает остальные)",
          "const onlyB = (n) => setOpenB({ 1: n === 1, 2: n === 2, 3: n === 3, 4: n === 4, 5: n === 5 })" in src)
    check("шагов в мастере ровно пять и переходы идут по порядку (next1..next4)",
          all(f"onClick: next{k}," in src for k in (1, 2, 3, 4)) and
          "onClick: next5," not in src,
          "переходы разъехались с нумерацией блоков")
    check("старая простыня шагов убрана (нет steps.map и s.kind ===)",
          "steps.map" not in src and "s.kind ===" not in src,
          "в файле остался старый список шагов")

    # 4) герметизация: один шаг в работе
    check("стража есть в runRerun (busyRef отсекает повторный вход)",
          "if (busyRef.current) return { ok: false, busy: true }" in src)
    check("стража есть в sendIntent (второй клик не копит вызовы)",
          "if (busyRef.current) return false" in src)
    check("все action-кнопки гаснут, пока шаг в работе (disabled: !!busy)",
          src.count("disabled: !!busy") >= 6 or src.count("disabled: !!busy ||") >= 4,
          f"{src.count('disabled: !!busy')}")

    # 5) тихий фоллбэк снят
    live = re.sub(r"/\*[\s\S]*?\*/", "", src)
    check("в живом коде нет sendIntent('rerun') (тихий уход в чат за LLM)",
          "sendIntent('rerun')" not in live,
          "остался тихий фоллбэк")
    check("сбой ядра назван и у него есть «Повторить» (rerunErr)",
          "setRerunErr(" in src and "⟳ Повторить" in src and "rerunErr" in src)
    check("прогон возвращает результат для перехода (ok/strategy/md)",
          "return { ok: true, strategy, md:" in src, "next1 не узнает, чем кончился прогон")

    # 6) красная граница = дефицит СВОЕГО шага (владелец: ввёл в поле «1» — получил
    #    красным блок 2, до которого дело не дошло): провал ПОЛУЧЕНИЯ источника
    #    краснит блок 1, блок 2 краснеет только когда источник уже на руках
    check("панель знает, ЧЕЙ это провал (rerunErrKind из out.failure_kind)",
          "setRerunErrKind(out.failure_kind || 'source')" in src,
          "провал снова безымянный — границы начнут путать шаги")
    check("провал прошлого прогона не наследуется (rerunErr и kind сбрасываются вместе)",
          "setRerunErr(''); setRerunErrKind('')" in src)
    # Правило владельца (дословно): «в группе 1 успешным является, если указанный в поле
    # файл или url существует/доступен. В противном случае - провал». Поэтому цвет границы
    # блока 1 берётся у ПРОБЫ источника, а не у разбора: у блока 2 своя ось.
    check("проба источника спрашивается при вводе (POST /probe, до разбора)",
          "ctx.rest('/probe'" in src and "src: asked" in src,
          "панель не проверяет, существует ли источник, а сразу его разбирает")
    check("блок 1 краснеет, когда источника нет, и зеленеет, когда он есть",
          "!srcProbe ? null : (srcProbe.ok ? 'done' : 'bad')" in src,
          "цвет блока 1 снова берётся не у пробы источника")
    check("блок 1 называет причину отказа словами пробы",
          "'источник не получен - ' + srcProbe.detail" in src,
          "причина провала источника не названа")
    check("«ДАЛЕЕ» блока 1 не открывается на неподтверждённом источнике",
          "(!!srcProbe && !srcProbe.ok)" in src and "'проверяю источник…'" in src,
          "шаг открывается, хотя источника нет")
    check("блок 2 краснеет только когда источник есть, а разбор не прошёл",
          "analyzed ? 'done' : (rerunErrKind === 'strategy' ? 'bad' : null)" in src,
          "красная граница блока 2 не привязана к 'strategy'")
    check("блок 2 не говорит «сорвался» про непройденный шаг 1 (ждёт источник)",
          "'ждёт источник'" in src)

    # 7) ЗЕЛЁНАЯ граница блока 1 = разобранный источник, а не любой ввод.
    #    Владелец: «просто цифра "1" приводит к зелёному цвету границы в блоке 1…
    #    это что — найденный валидный файл на диске, но на каком?». Ни файла, ни URL
    #    по «1» нет: зелёный брался из состояния ядра, где `src` пишется на ЛЮБОМ вводе
    #    (даже провальном), а прежний чужой отчёт подтверждал «анализ завершён».
    check("зелёный блок 1 требует, чтобы отчёт был СНЯТ С ЭТОГО источника",
          "report.chars != null && !reportOtherSrc" in src,
          "чужой отчёт снова выдаётся за завершённый анализ")
    check("отпечаток разбора при старте берётся у ОТЧЁТА (report_inputs), а не у поля ввода",
          "stt.report_inputs" in src and "stt.report_inputs" in src.split("const stt")[1][:600],
          "входы разбора при старте по-прежнему берутся из state.src")
    check("провал прогона снимает отпечаток (setFetchedSig(''))",
          src.count("setFetchedSig('')") >= 3,
          "после провала прежний отпечаток продолжает подтверждать разбор")

    # 8) Шарик у заголовка и плашка рядом говорят об ОДНОМ - о связи с ядром.
    #    Владелец: «шарик у заголовка "BOOK->SKILL" красный… значит плашка под ним должна
    #    выводить "нет связи"… но плашка зеленоватая и надпись "локальный режим"». Так и
    #    было: шарик краснел от `tone === 'error'` (любая локальная ошибка шага, 23 места)
    #    при живом ядре, а плашка читала `core`. Две оси у одной пары - обман.
    check("шарик читает ТОЛЬКО связь с ядром (core), а не ошибки шагов",
          "const dotTone = core === false ? 'bad' : core === true ? 'good' : 'warn'" in src
          and "tone === 'error'" not in src.split("const dotTone")[1][:80],
          "шарик снова краснеет от локальной ошибки при живом ядре")
    #    Владелец: «нет связи с ядром» - красноватый ФОН плашки и ОРАНЖЕВЫЕ буквы.
    #    Раньше стоял несуществующий у Badge SDK вариант `bad`: cva молча не подставлял
    #    ни фона, ни цвета - плашка выходила прозрачной с обычным текстом.
    check("молчащее ядро названо словами «нет связи с ядром», фон красный, буквы оранжевые",
          "нет связи с ядром" in src and "variant: 'destructive'" in src
          and "rgba(220, 38, 38, 0.32)" in src
          and "className: 'text-amber-600 dark:text-amber-300'" in src,
          "плашка молчит о связи, остаётся зелёной при красном шарике или теряет цвет")

    bad = [n for n, ok in checks if not ok]
    print(f"\nпроверок: {len(checks)}, провалов: {len(bad)}")
    if bad:
        print("провалено: " + "; ".join(bad))
        return 1
    print("ВСЁ ЗЕЛЁНОЕ: мастер из пяти блоков и детект markdown работают")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
