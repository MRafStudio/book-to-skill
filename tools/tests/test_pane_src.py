#!/usr/bin/env python3
"""Поле «Источник»: выбор файла и вставка из буфера — пиктограммами.

Зачем блок вообще нужен: владелец вводит источник руками, а рядом есть два
штатных пути — системный выбор файла и буфер обмена. Полные подписи
(«Выбрать файл», «Вставить из буфера обмена») в узкую панель не влезают: при
сжатии кнопка с длинным текстом либо выдавливает поле, либо превращается в
кашу. Решение — две кнопки-пиктограммы фиксированного размера, полный текст
уходит в нативный тултип (``title``) и в ``aria-label``.

Что проверяем
-------------
1. пиктограммы и ``looksLikePath`` вырезаются из ЖИВОГО ``plugin.js`` и
   исполняются в node (мок ``jsx``/``jsxs`` — React не нужен);
2. иконки — инлайновый SVG под ``currentColor``, БЕЗ текстовых детей: подпись
   внутри кнопки 12–14 px и есть та самая каша;
3. ``looksLikePath`` отличает полный путь от имени файла: имя файла подставлять
   в поле нельзя — ядро ушло бы искать его в cwd;
4. панель: у поля «Источник» две кнопки ``icon-xs`` + ``shrink-0`` с ``title``
   и ``aria-label``; инпут сжат (``minWidth: 0``), иначе кнопки выдавит за край;
5. у «Выбрать файл» есть путь к полному пути файла (мост Electron
   ``getPathForFile``) и честный фоллбэк с подсказкой про Ctrl+V; у вставки —
   ``navigator.clipboard.readText()`` и фокус в поле при отказе
   (``NotAllowedError``), а не молчаливое «ничего не произошло»;
6. ловушка ``children: Ell(`` (роняла панель в error-boundary) не вернулась.
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
  process.env.SVG_SRC, process.env.FOLDER_SRC, process.env.CLIP_SRC, process.env.LOOKS_SRC
].join('\n')
const api = new Function('jsx', 'jsxs', src + '\n; return { iconFolder, iconClipboard, looksLikePath }')(jsx, jsxs)
const { iconFolder, iconClipboard, looksLikePath } = api
const textish = (n) => typeof n === 'string' || typeof n === 'number'
/* Теги собираем отдельно от текста: сам маркер '<svg>' — тоже строка, и если
   мешать их в один массив, «текст внутри кнопки» находится там, где его нет. */
const collect = (node, acc) => {
  if (node == null) return acc
  if (Array.isArray(node)) { node.forEach((c) => collect(c, acc)); return acc }
  if (textish(node)) { acc.push(String(node)); return acc }
  if (node.props) {
    if (typeof node.type === 'string') acc.push({ tag: '<' + node.type + '>' })
    collect(node.props.children, acc)
  }
  return acc
}
const folder = iconFolder()
const clip = iconClipboard()
const out = {
  folder_tag: folder.type,
  folder_w: folder.props.width,
  folder_h: folder.props.height,
  folder_stroke: folder.props.stroke,
  folder_hidden: folder.props['aria-hidden'],
  folder_view: folder.props.viewBox,
  folder_children: folder.props.children.map((c) => c.type),
  folder_texts: collect(folder, []).filter(textish),
  clip_children: clip.props.children.map((c) => c.type),
  clip_texts: collect(clip, []).filter(textish),
  both_have_svg: folder.type === 'svg' && clip.type === 'svg',
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
        svg_src = src[src.index("const svgBox"):src.index("const iconFolder")]
        folder_src = src[src.index("const iconFolder"):src.index("const iconClipboard")]
        clip_src = src[src.index("const iconClipboard"):src.index("const looksLikePath")]
        looks_src = src[src.index("const looksLikePath"):].splitlines()[0] + "\n"
    except ValueError as exc:
        check("пиктограммы/looksLikePath извлекаются из plugin.js", False, str(exc))
        return 1
    body = svg_src + folder_src + clip_src + looks_src
    check("пиктограммы/looksLikePath извлекаются из plugin.js", len(body) > 400,
          f"длина {len(body)}", note=f"{len(body)} символов живого кода")

    with tempfile.TemporaryDirectory(prefix="b2s-pane-src-") as tmp:
        tmpd = Path(tmp)
        (tmpd / "run.mjs").write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ)
        env["SVG_SRC"], env["FOLDER_SRC"] = svg_src, folder_src
        env["CLIP_SRC"], env["LOOKS_SRC"] = clip_src, looks_src
        proc = subprocess.run(
            ["node", str(tmpd / "run.mjs")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )
    if proc.returncode != 0:
        check("пиктограммы исполняются в node", False, (proc.stderr or proc.stdout)[-600:])
        return 1
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    check("пиктограммы исполняются в node", True, note="чистые функции, без React")

    # 2) иконки — SVG под цвет темы, без текста внутри
    check("обе пиктограммы — svg", out["both_have_svg"], f"{out['folder_tag']!r}")
    check("пиктограмма рисуется currentColor (цвет темы, не картинка)",
          out["folder_stroke"] == "currentColor" and out["folder_view"] == "0 0 24 24",
          f"stroke={out['folder_stroke']!r} viewBox={out['folder_view']!r}")
    check("пиктограмма помечена aria-hidden (для скринридера её нет)",
          out["folder_hidden"] == "true", f"{out['folder_hidden']!r}")
    check("внутри кнопки нет текста — только фигуры",
          out["folder_texts"] == [] and out["clip_texts"] == []
          and out["folder_children"] == ["path"] and out["clip_children"] == ["rect", "path"],
          f"folder={out['folder_texts']!r} clip={out['clip_texts']!r} shapes={out['folder_children']!r}/{out['clip_children']!r}")
    check("у фигур буфера проставлены key (массив детей React без них ругается)",
          "key: 'r'" in clip_src and "key: 'b'" in clip_src and "key: 'f'" in folder_src,
          "нет key у фигур внутри пиктограммы")

    # 3) путь vs имя файла
    p = out["paths"]
    check("полный путь (D:/… и C:\\…) распознан",
          p["win"] and p["win_bs"] and p["nix"] and p["rel"], f"{p!r}")
    check("голое имя файла НЕ принимается за путь (иначе искали бы в cwd)",
          not p["bare"] and not p["empty"] and not p["spaces"], f"{p!r}")
    check("null/undefined/число не роняют проверку", not p["nul"] and not p["undef"] and not p["numbered"], f"{p!r}")
    check("URL из буфера подставляется (в нём есть слэш)", p["url"], f"{p!r}")

    # 4) панель: две компактные кнопки у поля
    field_from = src.index("label: 'Источник'")
    field = src[field_from:field_from + 2200]
    check("у поля «Источник» появилась кнопка выбора файла (пиктограмма)",
          "children: iconFolder()" in field, field[:200])
    check("у поля «Источник» появилась кнопка буфера (пиктограмма)",
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

    # 5) поведение обеих кнопок
    check("«Выбрать файл» берёт полный путь через мост Electron",
          "getPathForFile" in src and "window.hermesDesktop" in src, "нет пути к полному пути файла")
    check("«Выбрать файл» использует штатный input[type=file] (своего диалога у плагина нет)",
          "el.type = 'file'" in src and "el.click()" in src, "нет input[type=file]")
    check("отмена диалога не оставляет мусорный узел в документе",
          "addEventListener('focus'" in src and "drop()" in src, "нет уборки узла")
    check("имя файла без пути не подставляется молча — есть подсказка про Ctrl+V",
          "looksLikePath(full)" in src and "Ctrl+V" in src, "нет фоллбэка")
    check("«Вставить из буфера» читает буфер через clipboard.readText()",
          "navigator.clipboard.readText()" in src, "нет чтения буфера")
    check("отказ чтения буфера не молчит: фокус в поле + предупреждение",
          "srcRow.current" in src and "querySelector('input')" in src
          and "Hermes не дал прочитать буфер" in src, "нет обработки NotAllowedError")
    check("пустой буфер назван пустым, а не подставлен как мусор",
          "Буфер обмена пуст" in src, "нет ветки пустого буфера")
    check("результат шага виден тостом (host.notify), а не тишиной",
          "host.notify(" in src and "kind: 'success'" in src, "нет тоста")
    check("useRef импортирован (ref на строку поля)",
          "import { useEffect, useRef, useState } from 'react'" in src, "нет useRef в импорте react")
    check("ловушка children: Ell( не вернулась (роняла панель в error-boundary)",
          "children: Ell(" not in src, "найдено children: Ell(")

    bad = [n for n, ok in checks if not ok]
    print(f"\nпроверок: {len(checks)} · провалов: {len(bad)}")
    if bad:
        print("провалились: " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
