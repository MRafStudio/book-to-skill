#!/usr/bin/env python3
"""Заголовок блока «Результат разбора» — факт, а не константа.

Зачем: заголовок показывал одно и то же («готово · trafilatura · 55938 симв»)
независимо от того, был разбор, не был или провалился, — потому что панель
собирала его из ``history[0]`` (урезанная запись без ``junk_total``), а ядро
вообще не отдавало отчёт в ``/state`` (владелец: «в заголовке выводится одно и
то же текстовое сообщение! Даже если разбор не произведён, не производился или
был выполнен новый»). Второе требование — блок не раскрывается сам: если в
заголовке видно «55938 симв · мусор 0», лезть внутрь в 90% случаев незачем.

Что проверяем
-------------
1. ``headBitsOf`` извлекается из ЖИВОГО ``plugin.js`` (баланс скобок, не копия)
   и исполняется в node — без приложения и без React;
2. отчёта нет → «разбор не производился»; отчёт по ДРУГОМУ источнику →
   «отчёт по другому источнику» (иначе цифры врут про текущее поле);
3. отчёт по текущему источнику → «разбор готов» + симв + токенов + мусор,
   причём «мусор 0» печатается (ноль — это тоже факт, он-то и убирает нужду
   раскрывать блок);
4. занятость и ошибка перебивают метрики: «⏳ <что идёт>» / «⚠ ошибка»;
5. проваленные попытки каскада видны счётчиком;
6. панель НЕ раскрывает блок сама: ``setOutOpen(true)`` в файле отсутствует;
7. строка статуса живёт ВНЕ спойлера (``statusLine`` определён и стоит в дереве
   раньше ``resultBlock``);
8. ядро ``/state`` отдаёт сам отчёт (кроме ``preview``) и ``report_src`` —
   без этого панели не из чего собрать заголовок после перезагрузки.
"""
from __future__ import annotations

import json
import os
import re
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


RUNNER = r"""
import fs from 'node:fs'
const SRC = fs.readFileSync(process.env.HEAD_SRC, 'utf8')
const headBitsOf = new Function(SRC + '; return headBitsOf')()
const norm = (bits) => bits.map((b) => String(b).replace(/[\u00a0\u202f\u2009]/g, ' '))
const rep = {
  url: 'https://docs.python.org/3/library/pathlib.html', chars: 55938,
  est_tokens: 13984, junk_total: 0, words: 7842, strategy: 'trafilatura',
  at: '16:37', attempts: [{ ok: true, strategy: 'trafilatura' }]
}
const out = {}
out.none = norm(headBitsOf({ report: null, src: 'https://x', tone: 'idle', busy: '' }))
out.fresh = norm(headBitsOf({ report: rep, src: rep.url, tone: 'done', busy: '' }))
out.stale = norm(headBitsOf({ report: rep, src: 'https://docs.python.org/3/library/json.html', tone: 'idle', busy: '' }))
out.busy = norm(headBitsOf({ report: rep, src: rep.url, tone: 'working', busy: 'шаг 1 · загрузка и очистка источника…' }))
out.error = norm(headBitsOf({ report: rep, src: rep.url, tone: 'error', busy: '' }))
out.failed = norm(headBitsOf({
  report: Object.assign({}, rep, { attempts: [{ ok: false }, { ok: false }, { ok: true }] }),
  src: rep.url, tone: 'done', busy: ''
}))
out.junk = norm(headBitsOf({
  report: Object.assign({}, rep, { junk_total: 7 }), src: rep.url, tone: 'done', busy: ''
}))
console.log(JSON.stringify(out))
"""


def main() -> int:
    if not PLUGIN.is_file():
        print(f"нет файла плагина: {PLUGIN}")
        return 1
    src = PLUGIN.read_text(encoding="utf-8")

    # 1) вырезаем fmtInt + headBitsOf из живого файла
    try:
        fmt_line = src[src.index("const fmtInt"):src.index("const headBitsOf")]
        head_expr = cut_arrow_block(src, "const headBitsOf")
    except ValueError as exc:
        check("headBitsOf извлекается из plugin.js", False, str(exc))
        return 1
    body = fmt_line + "const headBitsOf = " + head_expr + "\n"
    check("headBitsOf извлекается из plugin.js", len(body) > 300,
          f"длина {len(body)}", note=f"{len(body)} символов живого кода")

    with tempfile.TemporaryDirectory(prefix="b2s-pane-head-") as tmp:
        tmpd = Path(tmp)
        (tmpd / "head.js").write_text(body, encoding="utf-8")
        (tmpd / "run.mjs").write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ)
        env["HEAD_SRC"] = str(tmpd / "head.js")
        proc = subprocess.run(
            ["node", str(tmpd / "run.mjs")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )
    if proc.returncode != 0:
        check("headBitsOf выполняется в node", False, (proc.stderr or proc.stdout)[-600:])
        return 1
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    check("headBitsOf выполняется в node", True, note="чистая функция, без React")

    # 2) состояние разбора
    check("без отчёта заголовок говорит «разбор не производился»",
          out["none"] and out["none"][0] == "разбор не производился", f"{out['none']!r}")
    check("отчёт по другому источнику помечен, а не выдан за текущий",
          any("другому источнику" in b for b in out["stale"]), f"{out['stale']!r}")
    check("свежий отчёт → «разбор готов»", out["fresh"] and out["fresh"][0] == "разбор готов",
          f"{out['fresh']!r}")
    check("занятость перебивает метрики («⏳ <шаг>»)",
          out["busy"] and out["busy"][0].startswith("⏳ шаг 1"), f"{out['busy']!r}")
    check("ошибка перебивает метрики", out["error"] and out["error"][0] == "⚠ ошибка",
          f"{out['error']!r}")

    # 3) метрики — именно те, что снимают нужду раскрывать блок
    fresh = " · ".join(out["fresh"])
    check("в заголовке есть объём в символах", "55 938 симв" in fresh, fresh)
    check("в заголовке есть «мусор 0» (ноль — тоже факт)", "мусор 0" in fresh, fresh)
    check("в заголовке есть «ошибок 0» — тот самый бит, ради которого не заходят внутрь",
          "ошибок 0" in fresh, fresh)
    check("в заголовке есть оценка токенов", "~13 984 токенов" in fresh, fresh)
    check("в заголовке есть время прогона", "в 16:37" in fresh, fresh)
    check("заголовок показывает источник отчёта действия (не константа)",
          any(b.startswith("разбор") for b in out["fresh"]) and "разбор" in out["none"][0], fresh)
    check("мусор ненулевой виден счётчиком",
          any("мусор 7" in b for b in out["junk"]), f"{out['junk']!r}")
    check("провалы каскада видны счётчиком ошибок",
          any("ошибок 2" in b for b in out["failed"]), f"{out['failed']!r}")

    check("во время работы шага в заголовке нет цифр прошлого прогона",
          "симв" not in " · ".join(out["busy"]), f"{out['busy']!r}")
    check("при ошибке заголовок не выдаёт метрики за текущие",
          "симв" not in " · ".join(out["error"]), f"{out['error']!r}")

    # 4) блок не раскрывается сам
    check("панель НЕ раскрывает блок сама (нет setOutOpen(true))",
          "setOutOpen(true)" not in src,
          "нашёл setOutOpen(true) — блок снова будет раскрываться автоматически")
    check("раскрытие осталось кликом (onToggle + setOutOpen)",
          "setOutOpen(!!" in src and "onToggle" in src)
    check("headBitsOf реально используется в панели (не мёртвый код)",
          "headBitsOf({" in src)

    # 5) строка статуса — вне блоков, видна всегда
    i_status = src.find("const statusLine")
    i_result = src.find("const resultBlock")
    i_use_status = src.find("      statusLine,")
    i_block2 = src.find("        n: 2,")
    check("строка статуса вынесена из спойлера отдельным узлом",
          i_status > 0 and i_status < i_result, f"statusLine@{i_status}, resultBlock@{i_result}")
    check("в дереве статус стоит раньше блока разбора (виден всегда)",
          i_use_status > 0 and i_block2 > i_use_status, f"use@{i_use_status}, block2@{i_block2}")
    check("блок разбора не содержит строку статуса внутри",
          not re.search(r"children: \[[\s\S]{0,400}?\bstatus\b\s*\?", src[i_result:i_result + 3000]),
          "внутри details снова печатается status")

    # 5б) Ловушка, на которой панель уже падала: Ell отдаёт props-объект, а не
    #     элемент. `children: Ell(...)` роняет рендер (React #31 «object with keys
    #     {className, style, title, children}») → error-boundary плагина, панель
    #     не грузится. В children идёт cutSpan, либо jsx('span', Ell(...)).
    check("в children нигде не подставлен raw-props Ell(...)",
          "children: Ell(" not in src, "нашёл 'children: Ell(' — панель упадёт в error-boundary")

    # 6) ядро отдаёт отчёт панели
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import api  # noqa: E402
        state = api.do_state()
    except Exception as exc:  # pragma: no cover
        check("ядро /state отдаёт отчёт", False, f"{type(exc).__name__}: {exc}")
    else:
        keys = set(state)
        check("ядро /state отдаёт отчёт панели (ключ report)", "report" in keys,
              f"ключи: {sorted(keys)}")
        check("ядро /state отдаёт источник отчёта (report_src)", "report_src" in keys)
        rep = state.get("report") or {}
        check("в отчёте нет preview (текст — отдельным маршрутом /text)",
              "preview" not in rep, f"ключи отчёта: {sorted(rep)[:12]}")
        if rep:
            check("отчёт несёт метрики для заголовка",
                  all(k in rep for k in ("chars", "junk_total", "strategy")),
                  f"ключи: {sorted(rep)[:12]}")
        else:
            check("отчёт несёт метрики для заголовка", True, note="стейт пуст — проверять нечего")

    failed = [n for n, ok in checks if not ok]
    print()
    print(f"проверок: {len(checks)}, провалов: {len(failed)}")
    if failed:
        print("провалено: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
