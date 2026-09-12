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
2. в дереве стоят ровно четыре блока ``PaneBlock``, у каждого — кнопка ``NextBtn``
   в правом нижнем углу (футер с ``min-w-0 flex-1``: подпись слева, кнопка справа);
3. переходы: ``next1`` (учитывает md), ``next2`` (требует отчёт), ``next3``
   (требует черновик в staging); шапка подписана «пропущен: файл уже markdown»;
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
  '\n; return { MD_EXT, stripQuery, isRemoteSrc, srcIsMarkdown }')(jsx, jsxs)
const { isRemoteSrc, srcIsMarkdown } = api
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
  clean: srcIsMarkdown('D:/a/b.md')
}))
"""


def main() -> int:
    if not PLUGIN.is_file():
        print(f"нет файла плагина: {PLUGIN}")
        return 1
    src = PLUGIN.read_text(encoding="utf-8")

    # 1) живые функции детекта — в node
    try:
        md_src = src[src.index("const MD_EXT"):src.index("const fmtInt")]
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

    # 2) четыре блока и футер «ДАЛЕЕ» в каждом
    blocks = re.findall(r"jsx\(PaneBlock, \{\n\s+n: (\d)", src)
    check("в дереве ровно четыре блока мастера (PaneBlock n: 1..4)",
          blocks == ["1", "2", "3", "4"], f"{blocks!r}")
    check("у каждого блока своя кнопка в футере (NextBtn)",
          src.count("jsx(NextBtn, {") == 4, f"{src.count('jsx(NextBtn, {')}")
    check("футер блока: подпись слева, кнопка справа (min-w-0 flex-1 перед foot)",
          src.count("min-w-0 flex-1") >= 1 and "justify-end" in src,
          "нет растяжки подписи перед кнопкой")
    check("каждый блок сворачивается кликом по шапке (каркас aria-expanded + onToggle ×4)",
          "'aria-expanded': open ? 'true' : 'false'" in src and
          src.count("onToggle: () => toggleB(") == 4,
          f"onToggle={src.count('onToggle: () => toggleB(')}")

    # 3) переходы
    check("переход из блока 1 учитывает готовый markdown (next1 → толькоB(3))",
          "const next1 = async () => {" in src and "onlyB(3)" in src,
          "нет ветки «сразу в блок 3»")
    check("переход из блока 2 требует отчёт о разборе (analyzed)",
          "const next2 = () => {" in src and "if (!analyzed)" in src,
          "«ДАЛЕЕ» блока 2 не проверяет разбор")
    check("переход из блока 3 требует черновик в staging (hasDraft)",
          "const next3 = () => {" in src and "if (!hasDraft)" in src,
          "«ДАЛЕЕ» блока 3 не проверяет staging")
    check("шапка пропущенного блока 2 подписана «пропущен: файл уже markdown»",
          "пропущен: файл уже markdown" in src)
    check("открытым может быть только один блок (onlyB сбрасывает остальные)",
          "const onlyB = (n) => setOpenB({ 1: n === 1, 2: n === 2, 3: n === 3, 4: n === 4 })" in src)
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
    print("ВСЁ ЗЕЛЁНОЕ: мастер из четырёх блоков и детект markdown работают")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
