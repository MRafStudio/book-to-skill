#!/usr/bin/env python3
"""Блок «Черновик скилла» — тот же дизайн, что у «Результата разбора».

Зачем: черновик скилла пишет LLM в чате, а панель о нём ничего не знает — файлы
появляются на диске сами по себе. Значит у блока две обязанности:
  * стоять СРАЗУ ПОД кнопкой шага 2 («Сделать черновик») — это его результат;
  * быть свёрнутым и НЕ раскрываться самому, потому что заголовок = факт с диска
    (файлы, главы, объём, время), и по нему уже видно, надо ли заходить внутрь
    (решение владельца: «дизайн тот же, точно так же должен уметь сворачиваться
    и разворачиваться»).

Что проверяем
-------------
1. ``draftBitsOf`` вырезается из ЖИВОГО ``plugin.js`` (не копия) и исполняется
   в node — без приложения и React;
2. «черновика нет» / «черновик другого имени» / «⏳ пишется» — разные состояния,
   и во время генерации цифры прошлого черновика в заголовок не примешиваются;
3. готовый черновик даёт файлы, главы, объём, термины и время;
4. ``draftRows`` группирует состав по смыслу (шапка → части → главы), а не по
   алфавиту: так состав читается с первого взгляда;
5. панель: блок свёрнут изначально (``useState(false)``), нет ``setDraftOpen(true)``,
   раскрытие — кликом, узел стоит в дереве шага черновика, внутри нет ``children: Ell(``
   (эта ловушка уже роняла панель в error-boundary);
6. ядро: ``/draft`` даёт сводку по диску, чужое имя НЕ подменяется свежим
   черновиком, текст файла читается только внутри каталога черновика (никакого
   выхода по ``../``), и ``/state`` несёт сводку для заголовка свёрнутого блока.
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


def cut_arrow_block(src: str, decl: str) -> str:
    """Вырезать значение ``const NAME = (…) => { … }`` по балансу фигурных скобок."""
    start = src.index(decl)
    eq = src.index("=", start)
    expr_start = src.index("(", eq)
    arrow = src.index("=>", expr_start)
    open_brace = src.index("{", arrow)
    depth = 0
    for i in range(open_brace, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[expr_start:i + 1]
    raise ValueError("не найден конец функции: " + decl)


def cut_function(src: str, decl: str) -> str:
    """Вырезать ``function NAME(…) { … }`` целиком (для объявлений, не стрелок)."""
    start = src.index(decl)
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise ValueError("не найден конец функции: " + decl)


RUNNER = r"""
import fs from 'node:fs'
const SRC = fs.readFileSync(process.env.DRAFT_SRC, 'utf8')
const load = new Function(SRC + '; return { draftBitsOf, draftRows, plural }')()
const { draftBitsOf, draftRows, plural } = load
const norm = (bits) => bits.map((b) => String(b).replace(/[\u00a0\u202f\u2009]/g, ' '))
const done = {
  ok: true, has_draft: true, name: 'python-pathlib', dir: 'D:/x/staging/python-pathlib',
  at: '17:20', glossary_terms: 108,
  counts: { files: 15, chapters: 10, chars: 119616, lines: 3120, words: 15200 },
  skill: { name: 'python-pathlib', description: 'Use when working with Python paths.', frontmatter: true },
  files: [
    { rel: 'chapters/ch02-pure-paths.md', kind: 'chapter', lines: 90, chars: 5000 },
    { rel: 'glossary.md', kind: 'part', lines: 110, chars: 6000 },
    { rel: 'SKILL.md', kind: 'index', lines: 93, chars: 6321 },
    { rel: 'metadata.json', kind: 'other', lines: 20, chars: 700 },
    { rel: 'cheatsheet.md', kind: 'part', lines: 185, chars: 14000 },
    { rel: 'chapters/ch01-basics.md', kind: 'chapter', lines: 80, chars: 4000 }
  ]
}
const out = {}
out.read = norm(draftBitsOf({ draft: null, want: '', busy: '' }))
out.empty = norm(draftBitsOf({ draft: { ok: true, has_draft: false, drafts: [] }, want: 'python-pathlib', busy: '' }))
out.working = norm(draftBitsOf({ draft: done, want: 'python-pathlib', busy: 'draft' }))
out.done = norm(draftBitsOf({ draft: done, want: 'python-pathlib', busy: '' }))
out.other = norm(draftBitsOf({ draft: done, want: 'wikipedia-kubernetes', busy: '' }))
out.nofront = norm(draftBitsOf({
  draft: Object.assign({}, done, { skill: { name: 'x', frontmatter: false } }), want: 'x', busy: ''
}))
out.rows = draftRows(done).map((r) => r.rel + ' ⟶ ' + r.label)
out.rows_kinds = draftRows(done).map((r) => r.rel)
out.rows_none = draftRows(null).length
out.plural = [plural(1, 'файл', 'файла', 'файлов'), plural(3, 'файл', 'файла', 'файлов'),
              plural(15, 'файл', 'файла', 'файлов'), plural(11, 'глава', 'главы', 'глав')]
console.log(JSON.stringify(out))
"""


def main() -> int:
    if not PLUGIN.is_file():
        print(f"нет файла плагина: {PLUGIN}")
        return 1
    src = PLUGIN.read_text(encoding="utf-8")

    # 1) вырезаем чистые функции из живого файла
    try:
        fmt_src = src[src.index("const fmtInt"):src.index("const headBitsOf")]
        plural_src = src[src.index("const plural"):src.index("function draftBitsOf")]
        bits_expr = (cut_arrow_block(src, "const draftBitsOf") if "const draftBitsOf" in src
                     else src[src.index("function draftBitsOf"):src.index("function Field")])
        rows_fn = cut_function(src, "function draftRows")
    except ValueError as exc:
        check("draftBitsOf/draftRows извлекаются из plugin.js", False, str(exc))
        return 1
    if "const draftBitsOf" in src:
        bits_body = "const draftBitsOf = " + bits_expr + "\n"
    else:
        bits_body = bits_expr + "\n"
    body = fmt_src + plural_src + bits_body + rows_fn + "\n"
    check("draftBitsOf/draftRows извлекаются из plugin.js", len(body) > 400,
          f"длина {len(body)}", note=f"{len(body)} символов живого кода")

    with tempfile.TemporaryDirectory(prefix="b2s-pane-draft-") as tmp:
        tmpd = Path(tmp)
        (tmpd / "draft.js").write_text(body, encoding="utf-8")
        (tmpd / "run.mjs").write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ)
        env["DRAFT_SRC"] = str(tmpd / "draft.js")
        proc = subprocess.run(
            ["node", str(tmpd / "run.mjs")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )
    if proc.returncode != 0:
        check("draftBitsOf выполняется в node", False, (proc.stderr or proc.stdout)[-600:])
        return 1
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    check("draftBitsOf выполняется в node", True, note="чистая функция, без React")

    # 2) состояния черновика разведены
    check("сводка не прочитана → так и сказано",
          out["read"] and out["read"][0] == "состояние черновика не прочитано", f"{out['read']!r}")
    # Ссылки на «шаг 2» здесь быть не должно: блок 2 — это АНАЛИЗ источника, и при
    # готовом markdown он пропускается. Раньше подпись звала именно туда, и человек
    # упирался в тупик («шаг 2 пропущен, а черновика нет»). Черновик делает кнопка
    # в ЭТОМ блоке — так и сказано.
    check("черновика нет → сказано, что его делает кнопка в этом блоке, и нет ссылки на «шаг 2»",
          out["empty"] and out["empty"][0] == "черновика «python-pathlib» в staging нет"
          and any("кнопка" in b for b in out["empty"])
          and not any("шаг 2" in b for b in out["empty"]), f"{out['empty']!r}")
    check("идёт генерация → «⏳ пишется», без цифр прошлого черновика",
          out["working"] and out["working"][0].startswith("⏳")
          and "симв" not in " · ".join(out["working"]), f"{out['working']!r}")
    check("готовый черновик → «черновик готов»", out["done"] and out["done"][0] == "черновик готов",
          f"{out['done']!r}")
    check("черновик другого имени назван, а не выдан за свой",
          any("а в поле" in b for b in out["other"]), f"{out['other']!r}")
    check("отсутствие шапки SKILL.md помечено (иначе Hermes скилл не увидит)",
          any("шапка" in b for b in out["nofront"]) or "frontmatter" in " · ".join(out["nofront"]),
          f"{out['nofront']!r}")

    # 3) цифры — те самые, ради которых не заходят внутрь
    joined = " · ".join(out["done"])
    check("в заголовке есть файлы с русским склонением", "15 файлов" in joined, joined)
    check("в заголовке есть главы", "10 глав" in joined, joined)
    check("в заголовке есть объём", "119 616 симв" in joined, joined)
    check("в заголовке есть термины и время", "108 терминов" in joined and "в 17:20" in joined, joined)
    check("склонения работают: 1 файл / 3 файла / 15 файлов / 11 глав",
          out["plural"] == ["файл", "файла", "файлов", "глав"], f"{out['plural']!r}")

    # 4) состав черновика
    rows = out["rows"]
    kinds = [r.split(" ⟶ ")[0] for r in out["rows_kinds"]]
    check("состав: шапка первой, затем справочные части, затем главы",
          rows and rows[0].startswith("SKILL.md")
          and kinds.index("glossary.md") < kinds.index("chapters/ch01-basics.md"),
          f"{rows!r}")
    check("в строке файла видны строки и объём",
          any("· 93 стр · 6321 симв" in r for r in rows), f"{rows!r}")
    check("пустой черновик не даёт строк (нет файлов — нет списка)", out["rows_none"] == 0,
          f"{out['rows_none']!r}")

    # 5) панель: блок свёрнут и стоит в своём шаге мастера
    i_details = src.find("const draftBlock = jsxs('details'")
    i_b3 = src.find("        n: 3,")
    i_b4 = src.find("        n: 4,")
    blk3 = src[i_b3:i_b4] if 0 < i_b3 < i_b4 else ""
    check("блок черновика — это details-спойлер (тот же дизайн, что у разбора)",
          i_details > 0 and "jsxs('details'" in src[i_details:i_details + 60],
          f"draftBlock@{i_details}")
    check("блок свёрнут изначально: useState(false)", "useState(false)" in src[
        src.find("const [draftOpen"):src.find("const [draftOpen") + 60],
        src[src.find("const [draftOpen"):src.find("const [draftOpen") + 60])
    check("панель НЕ раскрывает блок сама (нет setDraftOpen(true))",
          "setDraftOpen(true)" not in src,
          "нашёл setDraftOpen(true) — черновик раскроется сам")
    check("раскрытие — кликом (onToggle + setDraftOpen)",
          "setDraftOpen(!!" in src and "onToggle" in src)
    check("блок стоит в шаге 3 мастера (рядом с кнопкой «Сделать черновик»)",
          bool(blk3) and "draftBlock" in blk3 and "sendIntent('draft')" in blk3,
          f"block3@{i_b3}, len={len(blk3)}")
    check("внутри блока нет raw-props Ell(...) в children (React #31)",
          "children: Ell(" not in src, "нашёл 'children: Ell(' — панель упадёт в error-boundary")
    check("заголовок блока обёрнут в span (Ell даёт props, а не элемент)",
          "jsx('span', Ell('📝 Черновик скилла'" in src)
    check("список файлов под потолком, управление — отдельной строкой",
          "GROUP_CAP" in src[i_details:i_details + 4000] and
          src.find("⟳ обновить с диска") > i_details)

    # 5a) Зона списка файлов — «окно» панели, а не вуаль блока. Жалоба владельца:
    # «текстовое поле с прокруткой (в нём есть внедрённые кнопки глав и т.п.) должно
    # было выводиться с фоном всего плагина». Эталон — блок 2 (поле очищенного текста):
    # фон панели + рамка поля + data-glass-raised (под стеклом токен = transparent).
    i_rows = src.find("draftRowsList.map((r) => jsx(Button")
    listzone = src[max(0, i_rows - 800):i_rows] if i_rows > 0 else ""
    check("зона списка найдена (есть кнопки файлов)",
          i_rows > 0, f"draftRowsList@{i_rows}")
    check("список файлов на фоне панели, как поле в блоке 2 (PANEL_BG)",
          "backgroundColor: PANEL_BG" in listzone,
          "нет PANEL_BG у зоны списка — кнопки лягут на вуаль блока")
    check("под стеклом заливка не обнулится: data-glass-raised",
          "'data-glass-raised': ''" in listzone,
          "нет data-glass-raised — при mode=glass токен уйдёт в transparent")
    check("зона списка обрамлена рамкой поля (FIELD_LINE)",
          "border: FIELD_LINE" in listzone,
          "нет рамки — окно не отделяется от блока")
    check("потолок и прокрутка сохранены (группа не растёт в сосиску)",
          "Object.assign({}, GROUP_CAP" in listzone and "rounded px-1.5 py-1" in listzone,
          f"{listzone[-200:]!r}")

    # 5a-bis) Зона списка ГЛАВ («План по главам») — тоже «окно», решение владельца:
    # «согласен, надо дать ей такое же окно, как списку файлов». Та же болезнь: строки
    # раскладки лежали на вуали блока и читались как её текст.
    i_chap = src.find("chapterRowsList.map((line, i) =>")
    chapzone = src[max(0, i_chap - 600):i_chap] if i_chap > 0 else ""
    check("зона списка глав найдена (план по главам рисует строки)",
          i_chap > 0, f"chapterRowsList@{i_chap}")
    check("список глав — такое же окно панели, как список файлов",
          all(k in chapzone for k in ("backgroundColor: PANEL_BG", "border: FIELD_LINE",
                                      "'data-glass-raised': ''", "Object.assign({}, GROUP_CAP",
                                      "rounded px-1.5 py-1")),
          f"зона глав осталась вуалью блока: {chapzone[-160:]!r}")
    check("сводку можно перечитать, не раскрывая блок (кнопка «проверить staging»)",
          "проверить staging" in src)
    check("пока блок раскрыт, сводка перечитывается сама (таймер по draftOpen)",
          "if (!draftOpen) return undefined" in src and "setInterval" in src)

    # 5b) черновик пишет LLM в чате — панель обязана следить за staging сама.
    # Корень жалобы владельца: «Сделать черновик» отправляла задание и молчала, а
    # подпись звала в «шаг 2» (он же блок 2 — анализ), который при готовом markdown
    # пропускается. Получался тупик: кнопка вроде есть, но сделать нельзя.
    i_watch = src.find("const watchDraft")
    watch_body = src[i_watch:i_watch + 2000] if i_watch > 0 else ""
    check("есть вотчер черновика (панель не молчит после «Сделать черновик»)",
          i_watch > 0 and "setInterval" in watch_body and "/draft" in watch_body,
          f"watchDraft@{i_watch}")
    check("вотчер сдаётся по дедлайну и называет это честно",
          "900000" in watch_body and "не появился в staging" in watch_body,
          "вотчер будет ждать вечно или промолчит")
    check("интент draft запускает вотчер и поднимает «жду staging»",
          "if (kind === 'draft')" in src and "setDraftWait(true)" in src
          and "watchDraft((name || '').trim())" in src,
          "после «Сделать черновик» панель снова останется молчать")
    check("ожидание видно в шапке блока 3, а не только в строке статуса",
          "draftWait ? 'задание в чате — жду staging'" in src,
          "шапка блока 3 не знает про ожидание")
    check("при пропущенном блоке 2 подпись блока 3 говорит про markdown",
          "блок 2 пропущен: источник уже markdown" in src,
          "человек не поймёт, почему анализа нет, а черновик нужен")
    ui_step2 = [l for l in src.splitlines()
                if "шаг 2" in l and not l.lstrip().startswith(("*", "//", "/*"))]
    check("в подписях панели нет ссылок на «шаг 2» (блок 2 при markdown пропускается)",
          not ui_step2, f"осталось: {[l.strip()[:70] for l in ui_step2[:3]]}")

    # 5.1) фон кнопки-шага обязан совпадать с кликабельной зоной.
    # В колонке (`flex-col`) `align-items: stretch` тянул обёртку с фоном на всю ширину
    # панели, а кнопка внутри оставалась по тексту: владелец видел пустую плашку
    # («фон вытянут на всю ширину плагина, а активна только часть с надписью»).
    # Замер стендом: 380 px — фон 368 против кнопки 297 (дырка 71); с фиксом — 297 = 297.
    chap_slot = src[src.index("const chapterSlot"):src.index("/* --- переходы «ДАЛЕЕ»")]
    check("плашка кнопки плана не тянется на всю панель (align-self + max-width)",
          "alignSelf: 'flex-start'" in chap_slot and "maxWidth: '100%'" in chap_slot
          and "backgroundColor: STEP_BG" in chap_slot,
          "обёртка снова stretch: фон будет шире кликабельной кнопки")
    check("подпись кнопки плана — «План по главам: что слить, что переписать» (без номера)",
          "cutSpan('План по главам: что слить, что переписать'" in src
          and "3. План по главам" not in src,
          "номер у кнопки плана вернулся или подпись переименована")

    # 6) Тултипы кнопок — ОПИСАНИЕ действия, а не название кнопки. Просьба владельца:
    # «неплохо было бы при наведении указателя выдавать краткое описание того, что
    # кнопки будут делать, вместо того, чтобы выводить во всплывающих подсказках
    # название кнопок». Механика: третий аргумент у Ell/cutSpan; по умолчанию
    # (проза, подписи-метки) тултип остаётся самим текстом.
    check("Ell/cutSpan/fitLabel принимают тултип (третий аргумент)",
          "title: String(tip == null ? text : tip)" in src
          and "const cutSpan = (text, cls, tip)" in src
          and "const fitLabel = (label, tip)" in src,
          "тултип не прокинут — подсказка снова будет названием кнопки")
    check("карта описаний TIP объявлена и используется",
          "const TIP = {" in src and src.count("TIP.") >= 20,
          f"TIP.* использований: {src.count('TIP.')}")
    checks_tips = [
        ("Прогнать всё равно", "TIP.srcAnyway"),
        ("Прогнать заново", "TIP.srcRerun"),
        ("Анализ источника и очистка", "TIP.srcAnalyze"),
        ("⟳ Повторить", "TIP.retry"),
        ("Перегенерировать с учётом замечаний", "TIP.redraft"),
        ("План по главам: что слить, что переписать", "TIP.plan"),
        ("Критика и список правок", "TIP.review"),
        ("показать весь текст", "TIP.showText"),
        ("📄 SKILL.md", "TIP.skillMd"),
        ("⟳ проверить staging", "TIP.stagingCheck"),
        ("↗ Отправить агенту: решить, что слить", "TIP.planSend"),
    ]
    def tip_near(text, tip, win=420):
        # Подписи повторяются в файле (комментарии, карта TIP) — ищем вхождение,
        # рядом с которым реально стоит ключ описания, а не первое попавшееся.
        start = 0
        while True:
            i = src.find(text, start)
            if i < 0:
                return False
            if tip in src[i:i + win]:
                return True
            start = i + 1

    for label, tip in checks_tips:
        check(f"тултип кнопки «{label[:32]}» — описание действия, а не подпись",
              tip_near(label, tip), f"ни у одного вхождения подписи нет {tip}")
    check("тултип строки файла называет действие с именем файла",
          "title: TIP.openFile + r.label" in src)
    check("«Критика и список правок» получила иконку-символ",
          "children: '🔍'" in src)
    check("«Критика» гаснет без черновика и объясняет причину",
          "disabled: !!busy || !hasDraft" in src and "TIP.reviewOff" in src
          and "«Критика» включится, когда в staging появится черновик" in src,
          "нет ни гашения, ни причины")
    # «Критика и список правок» — рядовой кнопкой того же ряда: свой кегль 0.625rem
    # читался владельцем как «другое центрирование» (замер: flex-центр совпадал, 0.00 px,
    # отличался только размер шрифта). В ряду — один кегль и один цветовой токен.
    i_rev = src.find("sendIntent('review')")
    rev_btn = src[i_rev:i_rev + 700] if i_rev > 0 else ""
    check("«Критика и список правок» — тот же кегль и цвет, что у соседей по ряду",
          "text-xs text-(--ui-text-primary)" in rev_btn and "text-[0.625rem]" not in rev_btn,
          f"свой кегль у кнопки критики: {rev_btn[:220]!r}")
    bg_wraps = [l.strip()[:80] for l in src.splitlines() if "backgroundColor: STEP_BG" in l]
    check("плашки STEP_BG — у трёх кнопок-шагов (1, 2, 3)",
          len(bg_wraps) == 3, f"нашлось {len(bg_wraps)}: {bg_wraps}")
    check("кнопки в РЯДУ (шаг 1, черновик) не требуют фикса: родитель — items-center",
          src.count("'flex min-w-0 flex-wrap items-center gap-1'") >= 2,
          "рядок с items-center стало меньше двух — проверь, не растянулись ли их плашки")

    # 6) ядро
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import api  # noqa: E402
    except Exception as exc:  # pragma: no cover
        check("ядро отдаёт сводку черновика", False, f"{type(exc).__name__}: {exc}")
    else:
        try:
            live = api.do_draft("")
        except Exception as exc:
            live = {"has_draft": False, "error": str(exc)}
        if live.get("has_draft"):
            check("ядро читает черновик с диска",
                  live.get("counts", {}).get("files", 0) > 0 and live.get("counts", {}).get("chars", 0) > 0,
                  f"counts={live.get('counts')}", note=f"{live.get('name')}: "
                  f"{live.get('counts', {}).get('files')} файлов, {live.get('counts', {}).get('chapters')} глав")
            check("ядро отдаёт шапку SKILL.md (имя + description)",
                  bool(live.get("skill")) and "frontmatter" in live.get("skill", {}),
                  f"skill={live.get('skill')}")
            check("ядро перечисляет состав файлов с объёмом",
                  all(k in f for f in live.get("files", []) for k in ("rel", "kind", "lines", "chars")),
                  f"первый: {(live.get('files') or [{}])[0]}")
            check("SKILL.md в составе помечен как index (шапка, а не «прочее»)",
                  any(f["kind"] == "index" for f in live.get("files", [])),
                  f"{[f['kind'] for f in live.get('files', [])]}")
            one = live["files"][0]["rel"]
            got = api.do_draft_text(live["name"], one, 0, 400)
            check("текст файла черновика читается по имени",
                  got.get("ok") and got.get("chars", 0) > 0, f"{got.get('error')}")
        else:
            check("ядро читает черновик с диска", True, note="в staging черновиков нет — проверять нечего")
            check("ядро отдаёт шапку SKILL.md (имя + description)", True, note="staging пуст")
            check("ядро перечисляет состав файлов с объёмом", True, note="staging пуст")
            check("SKILL.md в составе помечен как index (шапка, а не «прочее»)", True, note="staging пуст")
            check("текст файла черновика читается по имени", True, note="staging пуст")

        got = api.do_draft("нет-такого-черновика-в-staging")
        check("чужое имя НЕ подменяется свежим черновиком",
              got.get("has_draft") is False and not got.get("files"),
              f"do_draft вернул чужой черновик: {got.get('name')}")
        check("при отсутствии черновика названы те, что есть (панель не гадает)",
              "drafts" in got, f"ключи: {sorted(got)}")

        bad = api.do_draft_text("", "../../SKILL.md", 0, 100)
        check("выход за каталог черновика закрыт (../ отвергнут)",
              bad.get("ok") is False and not bad.get("text"),
              f"do_draft_text отдал текст: {str(bad.get('text'))[:60]!r}")
        bad2 = api.do_draft_text("", "C:/Windows/win.ini", 0, 100)
        check("абсолютный путь вне staging отвергнут", bad2.get("ok") is False and not bad2.get("text"),
              f"отдал: {str(bad2.get('text'))[:60]!r}")

        state = api.do_state()
        check("ядро /state несёт сводку черновика (заголовок свёрнутого блока)",
              "draft" in state, f"ключи: {sorted(state)[:14]}")

    failed = [n for n, ok in checks if not ok]
    print()
    print(f"проверок: {len(checks)}, провалов: {len(failed)}")
    if failed:
        print("провалено: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
