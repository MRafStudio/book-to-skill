#!/usr/bin/env python3
"""Блок 5 «Запись в профиль» (файл исторически зовётся block4) — кнопка-обещание,
размер окна плана, сброс флага.

Зачем: блок записи делает ровно две вещи, и обе легко испортить незаметно.
  * Первый клик собирает план (ничего не пишет), второй пишет. Значит на кнопке
    ДО плана обязано быть написано «Предпросмотр», а после — «Установить»: иначе
    кнопка обещает запись, а делает чтение (владелец: «до первого клика должна
    быть надпись "Предпросмотр", и только после его выполнения — "Установить"»).
  * План — это список файлов, его ЧИТАЮТ. Короткий GROUP_CAP (7em ≈ 70 px) для
    него мал, поэтому у зоны своя высота (как у полей текста в блоках 2/3 —
    `max-h-72` = 288 px) и грип `resize: vertical` в правом нижнем углу.
  * План построен по входам (источник, имя, категория, режим, черновик). Любое
    действие в блоках 1–3 меняет входы — план обязан сбрасываться, иначе панель
    предлагает «Установить» по устаревшему плану.

Что проверяем
-------------
1. Вырезаем ЖИВОЙ `PREVIEW_CAP` и выражение подписи кнопки из `plugin.js` и
   исполняем их в node (без React и приложения);
2. подпись: без плана — «Предпросмотр», с планом — «Установить», у режима замены
   своя подпись (каталог сносится — это не рядовое обещание);
3. размер окна: `maxHeight 288`, `resize: vertical`, `overflowY: auto`, потолок в
   `em`-минимуме; зона плана берёт `PREVIEW_CAP`, а не `GROUP_CAP`;
4. поле плана скрыто, пока плана нет (`planBlock = preview ? … : null`);
5. `dropPreview` вырезается из тел семи действий (sendIntent, runRerun, runPlan,
   next1, loadText, loadDraftText, loadDraft) + страховка-эффект на смену входов;
6. тихий вызов `loadDraft(silent)` НЕ стирает план — это вотчер, а не нажатие.
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


def cut_braces(src: str, marker: str) -> str:
    """Вырезать объект/блок ``{ … }`` от маркера по балансу фигурных скобок."""
    start = src.index("{", src.index(marker))
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise ValueError("не найден конец блока: " + marker)


def cut_function(src: str, decl: str) -> str:
    """Вырезать ``function NAME(…) { … }`` целиком."""
    start = src.index(decl)
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise ValueError("не найден конец функции: " + decl)


def cut_arrow(src: str, decl: str) -> str:
    """Вырезать тело ``const NAME = (…) => { … }`` по балансу скобок."""
    start = src.index(decl)
    arrow = src.index("=>", start)
    open_brace = src.index("{", arrow)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace:i + 1]
    raise ValueError("не найдено тело: " + decl)


RUNNER = r"""
import fs from 'node:fs'
const SRC = fs.readFileSync(process.env.B4_SRC, 'utf8')
const load = new Function(SRC + '; return { PREVIEW_CAP, labelOf, planFileRows, planTotalRows }')()
const { PREVIEW_CAP, labelOf, planFileRows, planTotalRows } = load
const planOut = {
  plan: {
    added: Array.from({ length: 16 }, (_, i) => 'chapters/ch' + String(i + 1).padStart(2, '0') + '-new-name.md'),
    overwrite: ['SKILL.md', 'glossary.md'],
    keep: ['chapters/ch01-basics.md', 'cheatsheet.md', 'metadata.json']
  },
  existing_sources: [{ src: 'https://docs.python.org/3/library/pathlib.html' }],
  journal: { file: 'metadata.json', new_source: false, sources: 2, installs: 1 }
}
const out = {
  files: planFileRows(planOut),
  totals: planTotalRows(planOut),
  files_empty: planFileRows({ plan: {} }),
  cap: PREVIEW_CAP,
  none: labelOf(null, null),
  create: labelOf(null, { mode: 'create' }),
  append: labelOf(null, { mode: 'append' }),
  replace: labelOf(null, { mode: 'replace' }),
  installed_only: labelOf({ target: 'skills/rk7xml-interface/introduction' }, null),
  installed_with_plan: labelOf({ target: 'skills/rk7xml-interface/introduction' }, { mode: 'replace' })
}
console.log(JSON.stringify(out))
"""


def main() -> int:
    if not PLUGIN.is_file():
        print(f"нет файла плагина: {PLUGIN}")
        return 1
    src = PLUGIN.read_text(encoding="utf-8")

    # ── 1) живой код: потолок окна + подпись кнопки ─────────────────────────
    try:
        group_cap_src = "const GROUP_CAP = " + cut_braces(src, "const GROUP_CAP =")
        zone_cap_src = "const ZONE_CAP = " + cut_braces(src, "const ZONE_CAP =")
        cap_src = "const PREVIEW_CAP = " + cut_braces(src, "const PREVIEW_CAP =")
        m = re.search(r"label: (?:installed|preview)", src)
        if m is None:
            raise ValueError("не найдена подпись кнопки блока 4")
        i = m.start()
        j = src.index("onClick: () => runInstall", i)
        label_expr = src[i + len("label: "):j].strip().rstrip(",").strip()
    except ValueError as exc:
        check("PREVIEW_CAP и подпись кнопки извлекаются из plugin.js", False, str(exc))
        return 1
    check("PREVIEW_CAP и подпись кнопки извлекаются из plugin.js",
          len(group_cap_src + zone_cap_src + cap_src) > 120 and "preview" in label_expr,
          f"cap={len(cap_src)} симв, label={len(label_expr)} симв",
          note=f"подпись: {len(label_expr)} символов живого выражения")

    try:
        plan_files_fn = cut_function(src, "function planFileRows")
        plan_totals_fn = cut_function(src, "function planTotalRows")
    except ValueError as exc:
        check("planFileRows/planTotalRows извлекаются из plugin.js", False, str(exc))
        return 1
    body = (group_cap_src + "\n" + zone_cap_src + "\n" + cap_src + "\n" +
            plan_files_fn + "\n" + plan_totals_fn +
            "\nconst labelOf = (installed, preview) => (" + label_expr + ")\n")
    with tempfile.TemporaryDirectory(prefix="b2s-pane-b4-") as tmp:
        tmpd = Path(tmp)
        (tmpd / "b4.js").write_text(body, encoding="utf-8")
        (tmpd / "run.mjs").write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ)
        env["B4_SRC"] = str(tmpd / "b4.js")
        proc = subprocess.run(["node", str(tmpd / "run.mjs")], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=120, env=env)
    if proc.returncode != 0:
        check("подпись кнопки исполняется в node", False, (proc.stderr or proc.stdout)[-600:])
        return 1
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    check("подпись кнопки исполняется в node", True, note="без React и приложения")

    # ── 2) подпись: обещание совпадает с действием ─────────────────────────
    check("без плана кнопка обещает «Предпросмотр», а не запись",
          out["none"] == "Предпросмотр", f"{out['none']!r}")
    check("после предпросмотра (новый скилл) кнопка говорит «Установить»",
          out["create"] == "Установить", f"{out['create']!r}")
    check("после предпросмотра (долив) кнопка тоже говорит «Установить»",
          out["append"] == "Установить", f"{out['append']!r}")
    check("режим замены не маскируется под рядовую установку",
          out["replace"] == "Подтвердить ЗАМЕНУ", f"{out['replace']!r}")
    check("прежних подписей «Дополнить скилл» / «Заменить скилл» в панели нет",
          "Дополнить скилл" not in src and "Заменить скилл" not in src)

    # ── 2b) успешная установка гасит кнопку и говорит об этом словами ───────
    # Владелец: «после того, как в 4 блоке нажал установить и скилл успешно
    # установился, кнопка "Установить" остаётся активной - надо бы сделать
    # кнопку неактивной и написать на ней "Успешно установлен"».
    check("после успешной установки кнопка говорит «Успешно установлен»",
          out["installed_only"] == "Успешно установлен", f"{out['installed_only']!r}")
    check("оставшийся план не перебивает «Успешно установлен»",
          out["installed_with_plan"] == "Успешно установлен",
          f"{out['installed_with_plan']!r} - иначе второй клик предложит снести каталог заново")
    check("кнопка гаснет, пока цель установлена (disabled: … || !!installed)",
          re.search(r"disabled:\s*!!busy \|\| !draftReady \|\| !readySeen \|\| !!installed", src) is not None,
          "иначе повторный клик пишет в skills/… ещё раз")
    check("запись требует ГОТОВОГО черновика, а не просто каталога в staging",
          "disabled: !!busy || !draftReady || !readySeen" in src and
          "сначала пройди «ДАЛЕЕ» в шаге 4: запись открывается только после готового черновика" in src,
          "в профиль уедет обрывок: маркер готовности от LLM не проверяется")
    check("пишущийся черновик запирает запись и называет причину",
          "черновик ещё пишется - записывать нечего, дождись маркера готовности" in src and
          "черновик ещё пишется: ядро не пустит запись, пока LLM не положит маркер готовности (READY.json)" in src)
    check("факт установки ставится по факту записи, а не по «нет замечаний»",
          "setInstalled({ target: out.target" in src and "} else if (out.ok) {" in src and
          "if (out.dry_run === false || out.installed) {" in src,
          "ядро пишет файлы ДО проверки: ok=false с записанным скиллом - это не «не установили»")
    check("установка с замечаниями гасит кнопку (иначе второй клик начнёт долив)",
          re.search(r"dry_run === false \|\| out\.installed\) \{\s*\n\s*setInstalled\(", src) is not None,
          "кнопка оставалась «Установить» на уже установленном скилле")
    check("замечания проверки печатаются ТЕКСТОМ, а не «см. подробности ниже»",
          "const installIssues = (out) => {" in src and
          "out.error || 'см. подробности ниже'" not in src and
          "'⚠ ' + installIssues(preview)" in src,
          "владелец видел отказ без причины: подробностей в панели не было")
    check("шапка блока 4 называет каталог установки, а не «подтверди запись»",
          "установлен в ' + installed.target" in src and
          "state: installed" in src)
    check("под кнопкой сказано, что второй клик не нужен",
          "второй клик не нужен" in src)
    check("dropPreview сбрасывает и план, и факт установки (правка входов возвращает кнопку)",
          re.search(r"const dropPreview = \(\) => \{ setPreview\(null\); setInstalled\(null\) \}", src) is not None,
          "иначе кнопка «Успешно установлен» залипнет на новый скилл")
    check("установка сбрасывает ожидание черновика (watchDraft не тянет старое)",
          "setInstalled(null)" in src)
    check("тултип установленной кнопки называет цель",
          "'скилл уже записан в ' + installed.target" in src)

    # ── 3) окно плана: размер блока 2 + грип ───────────────────────────────
    cap = out["cap"]
    check("окно плана — высота как у полей текста блока 2 (max-h-72 = 288)",
          cap.get("maxHeight") == 288, f"maxHeight={cap.get('maxHeight')!r}")
    check("окно плана растягивается грипом за правый нижний угол",
          cap.get("resize") == "vertical", f"resize={cap.get('resize')!r}")
    check("окно плана скроллится и не тянет за собой панель",
          cap.get("overflowY") == "auto" and cap.get("overscrollBehavior") == "contain",
          f"overflowY={cap.get('overflowY')!r}")
    check("минимум окна в em — потолок выживает при крупном шрифте темы",
          str(cap.get("minHeight", "")).endswith("em"), f"minHeight={cap.get('minHeight')!r}")
    i_plan = src.index("const planBlock = preview")
    zone = src[i_plan:src.index("/* Раскладка по главам — подготовка шага 3")]
    check("окно плана берёт новое окно, а не короткий GROUP_CAP",
          "PREVIEW_CAP" in zone and "GROUP_CAP" not in zone,
          f"PREVIEW_CAP={'PREVIEW_CAP' in zone}, GROUP_CAP={'GROUP_CAP' in zone}")
    check("у окна плана подсказка про грип (объект объясняет себя)",
          "растянуть список файлов" in zone)

    # ── 3b) окно со списком файлов: по строке на файл, фон плагина, итоги внизу ──
    files = out["files"]
    check("файлы плана перечислены ПО СТРОКЕ НА ФАЙЛ, а не склейкой",
          isinstance(files, list) and len(files) == 21, f"строк: {len(files) if isinstance(files, list) else files!r}")
    check("в строке файла виден полный путь (не обрезанный огрызок)",
          all(r["file"].startswith(("chapters/", "SKILL.md", "glossary.md", "cheatsheet.md",
                                    "metadata.json")) for r in files),
          f"{files[:2]!r}")
    check("знак строки несёт действие: ＋ добавится, ⟳ перезапишется, ＝ останется",
          [r["mark"] for r in files[:1]] == ["＋"] and "⟳" in [r["mark"] for r in files]
          and "＝" in [r["mark"] for r in files],
          f"метки: {sorted(set(r['mark'] for r in files))}")
    check("первыми идут добавляемые файлы, потом перезапись, потом нетронутые",
          [r["mark"] for r in files] == ["＋"] * 16 + ["⟳"] * 2 + ["＝"] * 3,
          f"порядок меток: {[r['mark'] for r in files]}")
    check("пустой план не роняет список (окно говорит, что пусто)",
          out["files_empty"] == [], f"{out['files_empty']!r}")

    totals = out["totals"]
    check("внизу — счётчики словами: «добавится файлов: 16» и прочие подробности",
          totals and totals[0] == "добавится файлов: 16 · перезапишется: 2 · останется как есть: 3",
          f"{totals[:2]!r}")
    check("журнал источников остаётся в нижней части окна",
          any("журнал" in s for s in totals), f"{totals!r}")

    zone_start = src.index("/* Окно со списком файлов")
    zone2 = src[zone_start:zone_start + 1100]
    check("окно файлов — с фоном плагина и рамкой поля (как поле текста в блоке 2)",
          "backgroundColor: PANEL_BG" in zone2 and "border: FIELD_LINE" in zone2
          and "'data-glass-raised': ''" in zone2,
          "окно осталось вуалью блока — под стеклом заливка обнулится")
    check("внутри окна каждая строка — отдельный div (не один абзац)",
          "planFiles.map((r, i) => jsx('div', Ell(r.mark + ' ' + r.file)" in src)
    check("шапка и путь стоят ВНЕ окна (их не нужно прокручивать)",
          src.index("jsx('div', Ell(preview.target, 'opacity-80'))") < zone_start)
    check("прежняя склейка плана в одну строку удалена",
          "planRowsList" not in src and "…'" not in src.split("function planTotalRows")[0].split("function planFileRows")[1])

    # ── 4) поле плана скрыто, пока плана нет ───────────────────────────────
    tail = src[src.index("const planBlock = preview"):]
    check("поле предпросмотра скрыто при отсутствии плана",
          "const planBlock = preview" in src and "\n    : null" in tail[:tail.index("jsx(PaneBlock")],
          "поле плана рисуется всегда — сброс флага ничего не скрывает")

    # ── 5) сброс плана в действиях блоков 1–3 ──────────────────────────────
    # ── 4b) подписи ушли в тултип кнопки, а не висят рядом ──────────────────
    i4 = src.index("title: 'Запись в профиль'")
    seg4 = src[i4:src.index("foot: jsx(NextBtn", i4)]
    check("подписи-состояния рядом с кнопкой блока 4 нет (обе фразы — в тултипе)",
          "hint:" not in seg4, "подпись всё ещё висит рядом с кнопкой")

    # ── 4c) «не пахнет AI»: в ВЫВОДИМЫХ строках нет длинного тире ───────────
    def literals(s: str) -> str:
        """Только содержимое строковых литералов — комментарии не в счёт."""
        out, i, q = [], 0, None
        while i < len(s):
            c = s[i]
            if q is None:
                if c in "'\"`":
                    q = c; i += 1; continue
                if c == "/" and i + 1 < len(s) and s[i + 1] == "/":
                    j = s.find("\n", i); i = len(s) if j < 0 else j; continue
                if c == "/" and i + 1 < len(s) and s[i + 1] == "*":
                    j = s.find("*/", i); i = len(s) if j < 0 else j + 2; continue
                i += 1; continue
            if c == "\\":
                i += 2; continue
            if c == q:
                q = None; i += 1; continue
            out.append(c); i += 1
        return "".join(out)

    ui_text = literals(src)
    found = [hex(ord(ch)) for ch in ("\u2014", "\u2013") if ch in ui_text]
    check("в выводимых текстах нет длинного тире (U+2014/U+2013)", not found,
          f"осталось в строках: {found}")
    check("тултип «Предпросмотр» говорит, что план ничего не запишет",
          "«Предпросмотр» соберёт план и ничего не запишет - пишет только «Установить»" in src)
    check("тултип «Установить» говорит, куда пишет второй клик",
          "'второй клик пишет в skills/<категория>/<имя>/'" in src)
    check("dropPreview определён",
          "const dropPreview = () => { setPreview(null); setInstalled(null) }" in src)
    actions = {
        "sendIntent (черновик, критика, описание категории)": "const sendIntent = async (kind)",
        "runRerun (разбор источника)": "const runRerun = async ()",
        "runPlan (раскладка по главам)": "const runPlan = async ()",
        "next1 (переход по мастеру)": "const next1 = async ()",
        # Сигнатура может расширяться (у loadText появился второй аргумент - путь отчёта):
        # якорь держим по началу объявления, а не по закрытой скобке.
        "loadText (показать весь текст источника)": "const loadText = async (limit = 6000",
        "loadDraftText (открыть файл черновика)": "const loadDraftText = async (rel)",
    }
    for name, decl in actions.items():
        try:
            inner = cut_arrow(src, decl)
        except ValueError as exc:
            check(f"{name} сбрасывает план записи", False, str(exc))
            continue
        check(f"{name} сбрасывает план записи", "dropPreview()" in inner,
              "в теле действия нет dropPreview() — план останется устаревшим")

    draft_body = cut_arrow(src, "const loadDraft = async (silent = false)")
    check("клик «обновить с диска» сбрасывает план", "dropPreview()" in draft_body)
    marker = "if (!silent)"
    near = draft_body[draft_body.index(marker):draft_body.index(marker) + 120]
    check("тихий вызов вотчера план НЕ стирает",
          "dropPreview()" in near,
          "dropPreview стоит вне ветки !silent — вотчер (раз в 4 с) стирал бы план сам")

    check("смена входов РАЗБОРА (источник/режим/язык) сбрасывает план",
          "useEffect(() => { setPreview(null) }, [src, act, mode, lang, strat])" in src)
    check("правка имени/категории план НЕ сбрасывает, а пересобирает его (тихо, через дебаунс)",
          "setInstalled(null)" in src and "previewInstall(false) }, 700)" in src,
          "один чих в имени - и план пропадал")
    check("правка имени/категории НЕ трогает раскладку по главам",
          "setChapterPlan(null) }, [name, cat, act])" not in src)
    check("в блоке 4 нет children: Ell( (ловушка React #31)", "children: Ell(" not in src)

    # Реквизиты ЗАПИСИ живут в блоке 3, между разбором и черновиком: категория,
    # имя и описание категории спрашиваются ПОСЛЕ разбора (там видно имя из
    # заголовка), но ДО генерации - чтобы блок 4 (черновик) знал готовое имя и
    # каталог, а блок 5 сразу собирал план как долив или замену. Проверяем ПО
    # МЕСТУ В ФАЙЛЕ (после `n: 3,` и до `n: 4,`) - иначе переезд откатится, и
    # тесты этого не заметят.
    b3 = src.index("        n: 3,")
    b4 = src.index("        n: 4,")
    b5 = src.index("        n: 5,")
    cat_at = src.index("label: 'Категория скилла'")
    name_at = src.index("label: 'Имя скилла'")
    plan_at = src.index("          planBlock", b5)
    check("категория и имя скилла стоят в блоке 3 (между разбором и черновиком)",
          b3 < cat_at < b4 and b3 < name_at < b4,
          f"n3={b3}, cat={cat_at}, name={name_at}, n4={b4}")
    check("блок 1 больше не спрашивает имя и категорию",
          src.index("        n: 1,") < src.index("title: 'Источник',") < b3,
          "заголовок блока 1 должен быть про источник")
    check("окно плана живёт в блоке записи (n: 5), после черновика (n: 4)",
          b4 < b5 < plan_at, f"n4={b4}, n5={b5}, план={plan_at}")
    check("описание категории (чипса DESCRIPTION.md) внутри поля категории в блоке 3",
          cat_at < src.index(": catChip", cat_at) < name_at,
          "чипса описания осталась в блоке 1 или потерялась")
    check("определение чипсы описания не зависит от блока (живёт вне разметки)",
          src.index("const catChip = ") < src.index("        n: 1,"),
          "чипсу затащили внутрь блока 1")
    check("блок 3 говорит вслух, что реквизиты правятся свободно",
          "Это реквизиты ЗАПИСИ, а не входы разбора" in src)
    check("план долива/замены считается по имени из блока 3 (existing + act)",
          "const act = " in src and "existing && act === 'replace'" in src and
          src.index("label: 'Имя скилла'") < src.index("existing && act === 'replace'"),
          "выбор долива/замены разъехался с полем имени")

    failed = [n for n, ok in checks if not ok]
    print()
    print(f"проверок: {len(checks)}, провалов: {len(failed)}")
    if failed:
        print("провалено: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())