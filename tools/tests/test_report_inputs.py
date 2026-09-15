#!/usr/bin/env python3
"""Состояние ядра: «разобранный вход» — только удачный прогон.

Зачем проверка нужна. Панель при старте восстанавливает отпечаток разбора из
состояния ядра (``plugin.js``: ``stt.report_inputs``). Пока ядро не хранило входы
отчёта отдельно, панель брала их из ``state.src`` — а ``src`` ядро пишет на ЛЮБОМ
вводе, включая провальный. Получалась ложь с зелёным цветом:

    владелец ввёл в поле «Источник» голую «1» → прогон провалился →
    после перезапуска панели блок 1 светился ЗЕЛЁНОЙ границей «анализ завершён»,
    хотя файла «1» на диске нет и URL из «1» не выходит.

Владелец: «это что — найденный валидный файл на диске, но тогда на каком? Или это
валидный URL, но тогда куда? По моему — ни то ни другое предположение не валидно!»

Правило, которое сторожим:

* ``src`` — «что сейчас в поле»; пишется на любом вводе и попадает в ``last_error``;
* ``report`` и ``report_inputs`` — только удачный прогон: иначе провал выдаёт себя
  за разобранный источник.

Проверки
--------
1. свежее состояние: ``report_inputs`` пусто, отчёта нет;
2. провал по «1»: ``src`` записан, ``report_inputs`` НЕ появился, ``last_error.kind``
   = ``source`` (виноват шаг подачи, а не разбор);
3. удачный прогон: ``report_inputs`` = входы прогона (источник/стратегия/режим);
4. провал ПОСЛЕ успеха: входы удачного разбора не перезаписаны провалом;
5. «1» при стратегии под markdown всё равно ``source``: до разбора дело не дошло.

Каскад не гоняется: ``serve.Fetcher`` подменяется фейком, состояние — во временном
файле, поэтому ни сеть, ни ``dashboard/.state.json`` не задействованы.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import serve  # noqa: E402


class FakeFetcher:
    """Каскад без сети: отдаёт заранее заданный исход."""

    ok = True

    def __init__(self, force=None, **kw):
        self.force = force

    def fetch_to_dir(self, src: str, dest: str) -> dict:
        if not self.ok:
            return {"ok": False, "error": "нет доступа к источнику",
                    "attempts": [], "strategy": None, "chars": None}
        return {"ok": True, "strategy": "trafilatura", "chars": 1234,
                "junk_total": 7, "url": src, "attempts": [],
                "source_file": str(Path(dest) / "x.md")}


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, note: str = "") -> None:
        checks.append((name, bool(ok), note))

    tmp = Path(tempfile.mkdtemp(prefix="b2s_report_inputs_"))
    keep_state, keep_fetcher = serve.STATE_FILE, serve.Fetcher
    serve.STATE_FILE = tmp / ".state.json"
    try:
        # 1) свежее состояние — разбора нет вовсе
        serve.save_state(serve.initial_state())
        st = serve.load_state()
        check("свежее состояние: входов разбора нет", not st.get("report_inputs"))
        check("свежее состояние: отчёта нет", not st.get("report"))

        # 2) провал по «1» — ввод записан, разбора НЕТ
        serve.Fetcher = type("F", (FakeFetcher,), {"ok": False})
        st = serve.load_state()
        st["src"], st["strat"], st["mode"] = "1", "auto", "technical"
        report = serve.run_fetch(st)
        st = serve.load_state()
        err = st.get("last_error") or {}
        check("провал: ядро честно сказало ok=False", report.get("ok") is False)
        check("провал: ввод «1» записан в src (что сейчас в поле)",
              st.get("src") == "1")
        check("провал: входы разбора НЕ появились (нет выдуманного источника)",
              not st.get("report_inputs"))
        check("провал: отчёта по «1» нет", not st.get("report"))
        check("провал: провал назван виной шага 1 (kind=source)",
              err.get("kind") == "source",
              json.dumps(err, ensure_ascii=False))

        # 3) удачный прогон — входы разбора появляются
        serve.Fetcher = type("F", (FakeFetcher,), {"ok": True})
        st = serve.load_state()
        st["src"], st["strat"], st["mode"] = "D:/docs/guide.md", "auto", "technical"
        report = serve.run_fetch(st)
        st = serve.load_state()
        ri = st.get("report_inputs") or {}
        check("успех: отчёт сохранён", report.get("ok") is True)
        check("успех: входы разбора = источник/стратегия/режим прогона",
              ri.get("src") == "D:/docs/guide.md" and ri.get("strat") == "auto"
              and ri.get("mode") == "technical",
              json.dumps(ri, ensure_ascii=False))
        check("успех: провал прежнего прогона снят", not st.get("last_error"))

        # 4) провал ПОСЛЕ успеха — входы удачного разбора не перезаписаны
        serve.Fetcher = type("F", (FakeFetcher,), {"ok": False})
        st = serve.load_state()
        st["src"] = "1"
        serve.run_fetch(st)
        st = serve.load_state()
        ri = st.get("report_inputs") or {}
        check("провал после успеха: входы удачного разбора целы",
              ri.get("src") == "D:/docs/guide.md",
              json.dumps(ri, ensure_ascii=False))
        check("провал после успеха: отчёт не затёрт провалом",
              bool((st.get("report") or {}).get("ok")))

        # 5) «1» — вина шага 1, какую бы очистку ни выбрали
        check("«1» при стратегии под markdown — всё равно source",
              serve.failure_kind("1", "raw-md",
                                 {"ok": False, "attempts": []}) == "source")
    finally:
        serve.STATE_FILE, serve.Fetcher = keep_state, keep_fetcher

    for name, ok, note in checks:
        print(("OK   " if ok else "FAIL ") + name + (f"   [{note}]" if note else ""))
    bad = [name for name, ok, _ in checks if not ok]
    print(f"\nпроверок: {len(checks)}, провалов: {len(bad)}")
    if bad:
        print("провалено: " + "; ".join(bad))
        return 1
    print("ВСЁ ЗЕЛЁНОЕ: разобранным считается только удачно прогнанный источник")
    return 0


if __name__ == "__main__":
    sys.exit(main())
