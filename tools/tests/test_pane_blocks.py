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
   пояснительном комментарии), вместо него — ``rerunErr`` + кнопка «Повторить».
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
    check("блок 1 требует ТОЛЬКО источник: имя уехало в блок 3",
          "disabled: !!busy || !trimSrc," in src and
          "disabled: !!busy || !trimSrc || nameWarn" not in src,
          "имя снова стало входом разбора")
    check("пустое имя гасит «ДАЛЕЕ» блока 3 и установку в блоке 5, а не разбор",
          "disabled: !!busy || nameWarn || nameBad," in src and
          "disabled: !!busy || !draftReady || !readySeen || !!installed || nameWarn || nameBad," in src and
          "disabled: !!busy || !hasDraft,   // имя" not in src,
          "пустое имя стало входом разбора или потеряло замок")
    check("имя обязательно там, где пишется каталог (блок 5)",
          "disabled: !!busy || !draftReady || !readySeen || !!installed || nameWarn || nameBad," in src and
          "нужно имя скилла: пустым не поставим - каталог в skills/ должен быть назван" in src,
          "установка пойдёт без имени")
    # Имя, которое линза Hermes не примет, - такая же незаполненная обязательная
    # строка, как пустая, только ошибка вылезала ПОСЛЕ записи: скилл ложился в
    # профиль с шапкой `name: license-Info`, а линза отвечала «must be lowercase».
    check("недопустимое имя (заглавные, пробелы) ловится ДО записи",
          "const nameBad = !nameWarn && !/^[a-z0-9][a-z0-9._-]*$/.test(wanted)" in src and
          "блок 3 · такое имя Hermes не примет" in src and
          "if (nameBad)" in src,
          "имя с заглавной снова доедет до установки и упадёт на линзе")
    check("подпись недопустимого имени называет правило, а не только красит",
          "только строчные латинские буквы, цифры" in src,
          "владелец видит жёлтое поле без причины")

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
          "setFetchedSig(analysisSigOf({ src, strat, mode }))" in src,
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
    check("устаревший отчёт назван словами и в шапке блока, и в спойлере",
          "staleReport" in src and "от прежних входов" in src and
          "отчёт ниже - от прежних входов, а не от этих" in src)
    check("кнопка шага 1 при работе говорит «Идёт разбор…», а не обещает результат",
          "busy === 'rerun' ? 'Идёт разбор…'" in src and "busy === 'rerun' ? '⏳'" in src)

    # 2) пять блоков и футер «ДАЛЕЕ» в каждом
    blocks = re.findall(r"jsx\(PaneBlock, \{\n\s+n: (\d)", src)
    check("в дереве ровно пять блоков мастера (PaneBlock n: 1..5)",
          blocks == ["1", "2", "3", "4", "5"], f"{blocks!r}")
    check("у каждого блока своя кнопка в футере (NextBtn)",
          src.count("jsx(NextBtn, {") == 5, f"{src.count('jsx(NextBtn, {')}")
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

    bad = [n for n, ok in checks if not ok]
    print(f"\nпроверок: {len(checks)}, провалов: {len(bad)}")
    if bad:
        print("провалено: " + "; ".join(bad))
        return 1
    print("ВСЁ ЗЕЛЁНОЕ: мастер из пяти блоков и детект markdown работают")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
