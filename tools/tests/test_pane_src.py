#!/usr/bin/env python3
"""Поле «Источник»: выбор файла и вставка из буфера — цветными ярлыками.

Зачем блок вообще нужен: владелец вводит источник руками, а рядом есть два
штатных пути — системный выбор файла и буфер обмена. Полные подписи
(«Выбрать файл», «Вставить из буфера обмена») в узкую панель не влезают: при
сжатии кнопка с длинным текстом либо выдавливает поле, либо превращается в
кашу. Решение — две кнопки-ярлыка фиксированного размера, полный текст уходит
в нативный тултип (``title``) и в ``aria-label``.

Две правки по следам замечаний владельца («ярлыки не цветные… раздражают»,
«кнопка вставить не работает»):

* ярлык — ЦВЕТНОЙ эмодзи (``📂``/``📋``), а не монохромный SVG под
  ``currentColor``: серая линия в панели не читается как ярлык. Чтобы эмодзи в
  кнопке 24×24 не плыл по базовой линии, кегль и ``lineHeight`` заданы инлайном;
* буфер читает ШТАТНАЯ дверь приложения ``window.hermesDesktop.readClipboard()``
  (IPC ``hermes:readClipboard`` → ``clipboard.readText()`` в main): браузерный
  ``navigator.clipboard.readText()`` в Electron отказывает, когда документ не в
  фокусе (``electron/main.ts`` говорит об этом прямо) — из-за него кнопка и
  молчала. ``navigator`` оставлен фоллбэком;
* файл выбирает нативный диалог ``window.hermesDesktop.selectPaths()`` (IPC
  ``hermes:selectPaths``), который отдаёт готовые ПОЛНЫЕ пути; ``<input
  type=file>`` + ``getPathForFile`` — фоллбэк для сборки без этой двери.

Что проверяем
-------------
1. живые куски вырезаются из ``plugin.js`` и исполняются в node (мок
   ``jsx``/``jsxs`` — React не нужен);
2. ярлыки: ``span`` с эмодзи (НЕ svg), фиксированные кегль/``lineHeight``,
   ``aria-hidden``;
3. ``baseNameOf`` режет и Windows-, и Unix-пути, не роняет пустое/None;
4. ``looksLikePath`` отличает полный путь от имени файла: имя файла подставлять
   в поле нельзя — ядро ушло бы искать его в cwd;
5. панель: у поля «Источник» две кнопки ``icon-xs`` + ``shrink-0`` с ``title`` и
   ``aria-label``; инпут сжат (``minWidth: 0``);
6. порядок дверей: ``readClipboard`` вызывается РАНЬШЕ ``navigator.clipboard``,
   ``selectPaths`` — раньше ``<input type=file>``; отказ и пустой буфер не молчат;
7. штатные двери реально объявлены в сборке Hermes (``global.d.ts``), если она
   под рукой — иначе проверка помечается «пропущено»;
8. ловушка ``children: Ell(`` (роняла панель в error-boundary) не вернулась.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js"
GLOBAL_DTS = Path("D:/NEURO/Hermes/data/hermes/hermes-agent/apps/desktop/src/global.d.ts")

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    if ok:
        print(f"  OK   {name}" + (f" — {note}" if note else ""))
    else:
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


RUNNER = r"""
const jsx = (type, props) => ({ type, props: props || {} })
const jsxs = (type, props) => ({ type, props: props || {} })
const src = [
  process.env.GLYPH_SRC, process.env.BASE_SRC, process.env.LOOKS_SRC
].join('\n')
const api = new Function('jsx', 'jsxs', src + '\n; return { EMOJI_FIT, iconFolder, iconClipboard, baseNameOf, looksLikePath }')(jsx, jsxs)
const { EMOJI_FIT, iconFolder, iconClipboard, baseNameOf, looksLikePath } = api
const textish = (n) => typeof n === 'string' || typeof n === 'number'
const texts = (node, acc) => {
  if (node == null) return acc
  if (Array.isArray(node)) { node.forEach((c) => texts(c, acc)); return acc }
  if (textish(node)) { acc.push(String(node)); return acc }
  if (node.props) texts(node.props.children, acc)
  return acc
}
const folder = iconFolder()
const clip = iconClipboard()
const out = {
  folder_tag: folder.type,
  folder_texts: texts(folder, []),
  folder_style: folder.props.style,
  folder_hidden: folder.props['aria-hidden'],
  clip_tag: clip.type,
  clip_texts: texts(clip, []),
  clip_style: clip.props.style,
  emoji_fit: EMOJI_FIT,
  base: {
    win: baseNameOf('D:/src/docs/a.md'),
    win_bs: baseNameOf('C:\\docs\\file.pdf'),
    nix: baseNameOf('/home/u/book.txt'),
    bare: baseNameOf('book.pdf'),
    empty: baseNameOf(''),
    nul: baseNameOf(null),
    undef: baseNameOf(undefined),
    url: baseNameOf('https://docs.python.org/3/library/pathlib.html'),
    trailing: baseNameOf('D:/src/docs/')
  },
  paths: {
    win: looksLikePath('D:/src/docs/a.md'),
    win_bs: looksLikePath('C:\\docs\\file.pdf'),
    nix: looksLikePath('/home/u/book.txt'),
    rel: looksLikePath('docs/chapter.md'),
    bare: looksLikePath('book.pdf'),
    empty: looksLikePath(''),
    spaces: looksLikePath('   '),
    nul: looksLikePath(null),
    undef: looksLikePath(undefined),
    url: looksLikePath('https://docs.python.org/3/library/pathlib.html'),
    numbered: looksLikePath(42)
  }
}
console.log(JSON.stringify(out))
"""


def main() -> int:
    if not PLUGIN.is_file():
        print(f"нет файла плагина: {PLUGIN}")
        return 1
    src = PLUGIN.read_text(encoding="utf-8")

    # 1) вырезаем живые куски
    try:
        glyph_src = src[src.index("const EMOJI_FIT"):src.index("const baseNameOf")]
        base_src = src[src.index("const baseNameOf"):].splitlines()[0] + "\n"
        looks_src = src[src.index("const looksLikePath"):].splitlines()[0] + "\n"
    except ValueError as exc:
        check("ярлыки/baseNameOf/looksLikePath извлекаются из plugin.js", False, str(exc))
        return 1
    body = glyph_src + base_src + looks_src
    check("ярлыки/baseNameOf/looksLikePath извлекаются из plugin.js", len(body) > 300,
          f"длина {len(body)}", note=f"{len(body)} символов живого кода")

    with tempfile.TemporaryDirectory(prefix="b2s-pane-src-") as tmp:
        tmpd = Path(tmp)
        (tmpd / "run.mjs").write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ)
        env["GLYPH_SRC"], env["BASE_SRC"], env["LOOKS_SRC"] = glyph_src, base_src, looks_src
        proc = subprocess.run(
            ["node", str(tmpd / "run.mjs")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )
    if proc.returncode != 0:
        check("ярлыки исполняются в node", False, (proc.stderr or proc.stdout)[-600:])
        return 1
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    check("ярлыки исполняются в node", True, note="чистые функции, без React")

    # 2) ярлыки — цветные эмодзи, а не монохромный svg
    check("ярлык — эмодзи в span (монохромный svg владелец забраковал)",
          out["folder_tag"] == "span" and out["clip_tag"] == "span"
          and out["folder_texts"] == ["📂"] and out["clip_texts"] == ["📋"],
          f"tag={out['folder_tag']!r}/{out['clip_tag']!r} texts={out['folder_texts']!r}/{out['clip_texts']!r}")
    check("в plugin.js не осталось svgBox (серая currentColor-иконка)",
          "const svgBox" not in src and "stroke: 'currentColor'" not in src,
          "остался svg-ярлык")
    fit = out["emoji_fit"]
    check("эмодзи прижат инлайном по базовой линии (lineHeight 1 + кегль)",
          fit.get("lineHeight") == 1 and isinstance(fit.get("fontSize"), int)
          and fit.get("display") == "block",
          f"style={fit!r}")
    check("кегль эмодзи держится в пределах кнопки 24 px", 11 <= (fit.get("fontSize") or 0) <= 15,
          f"fontSize={fit.get('fontSize')!r}")
    check("ярлык помечен aria-hidden (для скринридера он не подпись)",
          out["folder_hidden"] == "true", f"{out['folder_hidden']!r}")

    # 3) имя файла из пути — для человеческого тоста
    b = out["base"]
    check("baseNameOf режет Windows-путь",
          b["win"] == "a.md" and b["win_bs"] == "file.pdf", f"{b!r}")
    check("baseNameOf режет Unix-путь и голое имя не портит",
          b["nix"] == "book.txt" and b["bare"] == "book.pdf", f"{b!r}")
    check("baseNameOf не роняет пустое/None/undefined",
          b["empty"] == "" and b["nul"] == "" and b["undef"] == "", f"{b!r}")
    check("baseNameOf берёт последний сегмент у URL и терпит хвостовой слэш",
          b["url"] == "pathlib.html" and b["trailing"] == "docs", f"{b!r}")

    # 4) путь vs имя файла
    p = out["paths"]
    check("полный путь (D:/… и C:\\… и /home/… и относительный) распознан",
          p["win"] and p["win_bs"] and p["nix"] and p["rel"], f"{p!r}")
    check("голое имя файла НЕ принимается за путь (иначе искали бы в cwd)",
          not p["bare"] and not p["empty"] and not p["spaces"], f"{p!r}")
    check("null/undefined/число не роняют проверку",
          not p["nul"] and not p["undef"] and not p["numbered"], f"{p!r}")
    check("URL из буфера подставляется (в нём есть слэш)", p["url"], f"{p!r}")

    # 5) панель: две компактные кнопки у поля
    field_from = src.index("label: 'Источник'")
    field = src[field_from:field_from + 2400]
    check("у поля «Источник» появилась кнопка выбора файла (ярлык)",
          "children: iconFolder()" in field, field[:200])
    check("у поля «Источник» появилась кнопка буфера (ярлык)",
          "children: iconClipboard()" in field, field[:200])
    check("кнопки фиксированного размера icon-xs и не сжимаются",
          field.count("size: 'icon-xs'") == 2 and field.count("className: 'shrink-0'") == 2,
          f"icon-xs: {field.count(chr(39) + 'icon-xs' + chr(39))}, shrink-0: {field.count('shrink-0')}")
    check("полная подпись ушла в тултип и в aria-label",
          "title: 'Выбрать файл на диске" in field and "'aria-label': 'Выбрать файл'" in field
          and "title: 'Вставить из буфера обмена" in field and "'aria-label': 'Вставить из буфера обмена'" in field,
          "нет title/aria-label хотя подпись сокращена")
    check("поле сжато (minWidth 0 + flex), иначе кнопки выдавит за край",
          "minWidth: 0, flex: '1 1 auto'" in field, "нет minWidth: 0 у инпута")
    check("строка поля обёрнута в flex-контейнер с ref (нужен для фокуса)",
          "ref: srcRow" in field and "flex items-center gap-1" in field, field[:200])
    check("подписи кнопок не превратились в текст внутри кнопки",
          "cutSpan('Выбрать" not in field and "cutSpan('Вставить" not in field, "текст вернулся в кнопку")

    # 6) поведение: сначала штатные двери, фоллбэки — после
    pick_from = src.index("const pickFile =")
    fall_from = src.index("const pickFileFallback")
    pick = src[pick_from:fall_from]
    fall = src[fall_from:src.index("const pasteSrc")]
    check("«Выбрать файл» идёт в нативный диалог selectPaths (полные пути, без DOM-вози)",
          "bridge.selectPaths(" in pick, "нет вызова штатного диалога")
    check("диалог зовётся с одиночным выбором и фильтрами по документам",
          "multiple: false" in pick and "filters: SRC_FILTERS" in pick and "'pdf'" in src,
          "нет multiple/filters")
    check("отмена диалога названа, а не молчит", "Диалог закрыт без выбора" in pick, "нет ветки отмены")
    check("фоллбэк на input[type=file] остался (сборка без selectPaths)",
          "pickFileFallback()" in pick and "el.type = 'file'" in fall and "el.click()" in fall,
          "нет фоллбэка")
    check("фоллбэк берёт полный путь мостом getPathForFile",
          "getPathForFile" in fall and "window.hermesDesktop" in fall, "нет моста в фоллбэке")
    check("имя файла без пути не подставляется молча — есть подсказка про Ctrl+V",
          "looksLikePath(full)" in fall and "Ctrl+V" in fall, "нет честного фоллбэка")
    check("отмена диалога не оставляет мусорный узел в документе (фоллбэк)",
          "addEventListener('focus'" in fall and "drop()" in fall, "нет уборки узла")
    # Состояние блока 1 - по ФАКТУ разбора, а не по виду входа: «ДАЛЕЕ» блока 1
    # запускает анализ, и заголовок обязан это показать (владелец: «когда анализ
    # завершён, можно менять состояние на "1. Источник - URL - анализ завершён"»).
    check("заголовок блока 1 называет завершённый анализ",
          "URL - анализ завершён" in src and "файл - анализ завершён" in src,
          "состояние висит на «нужен разбор» после завершённого анализа")
    check("во время разбора заголовок блока 1 говорит, что идёт работа",
          "URL - разбираю…" in src and "иду разбор источника" in src,
          "нет промежуточного состояния")
    check("завершённый анализ красит блок 1 зелёным, а непройденный источник - красным",
          "mdSrc || analyzed" in src and "? 'done'" in src and "busy === 'rerun' ? 'working'" in src
          and "rerunErrKind === 'source' ? 'bad'" in src, "цвет блока 1 не связан с состоянием разбора")

    paste_from = src.index("const pasteSrc")
    paste = src[paste_from:src.index("const loadText")]
    i_bridge = paste.find("bridge.readClipboard()")
    i_nav = paste.find("navigator.clipboard.readText()")
    check("«Вставить из буфера» читает штатную дверь readClipboard",
          i_bridge > 0, "нет вызова window.hermesDesktop.readClipboard")
    check("readClipboard вызывается РАНЬШЕ браузерного navigator.clipboard",
          i_bridge > 0 and i_nav > 0 and i_bridge < i_nav,
          f"порядок: readClipboard@{i_bridge}, navigator@{i_nav}")
    check("штатная дверь проверяется на существование (старая сборка не роняет кнопку)",
          "typeof bridge.readClipboard === 'function'" in paste, "нет guard'а")
    check("отказ чтения буфера не молчит: фокус в поле + предупреждение",
          "srcRow.current" in paste and "querySelector('input')" in paste
          and "Ctrl+V" in paste, "нет обработки отказа")
    check("ошибка названа словами (что именно отказало), а не проглочена",
          "err.message" in paste and "Буфер обмена не прочитался" in paste, "нет текста ошибки")
    check("пустой буфер назван пустым, а не подставлен как мусор",
          "Буфер обмена пуст" in paste, "нет ветки пустого буфера")
    check("успех назван и виден тостом (host.notify + kind success)",
          "host.notify(" in paste and "kind: 'success'" in paste and "из буфера" in paste, "нет тоста")
    check("useRef импортирован (ref на строку поля)",
          "import { useEffect, useRef, useState } from 'react'" in src, "нет useRef в импорте react")
    check("ловушка children: Ell( не вернулась (роняла панель в error-boundary)",
          "children: Ell(" not in src, "найдено children: Ell(")

    # 7) штатные двери — не выдумка: сверяем со сборкой Hermes, если она под рукой
    if GLOBAL_DTS.is_file():
        dts = GLOBAL_DTS.read_text(encoding="utf-8", errors="replace")
        check("двери selectPaths/readClipboard объявлены в сборке Hermes",
              "selectPaths" in dts and "readClipboard" in dts, "нет в global.d.ts",
              note=str(GLOBAL_DTS))
    else:
        check(f"сборка Hermes под рукой ({GLOBAL_DTS.name}) — проверка дверей", True,
              note="пропущено: файла сборки нет, доверяем preload/main")

    bad = [n for n, ok in checks if not ok]
    print(f"\nпроверок: {len(checks)} · провалов: {len(bad)}")
    if bad:
        print("провалились: " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
