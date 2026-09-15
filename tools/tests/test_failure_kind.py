#!/usr/bin/env python3
"""Чей это провал: получение источника (шаг 1) или разбор (шаг 2).

Зачем: панель краснит тот блок, где не хватает обязательного. Пока ядро не
говорило, ЧТО сорвалось, панель красила оба блока сразу — и владелец получал
красную границу у «Анализа источника», хотя в поле стояла просто «1» и до
разбора дело не дошло:

    «Давай не делать у группы 2 красную границу, если в группе 1 источник не
     прошёл валидацию (не удалось загрузить, отсутствует доступ) я, например,
     просто ввёл в поле "Источник" цифру 1. А получил - группу 2 "Анализ
     источника" с красной границей...»
    «Я бы сказал в случае группы 1 - "не удалось получить источник"»

Что проверяем
-------------
1. ``failure_kind`` живёт в ядре (``tools/serve.py``) и вызывается на реальных
   парах «источник + стратегия», а не на копии логики;
2. недоступный/несуществующий источник → ``'source'``: в поле «1», битая ссылка,
   пустое поле — всё это шаг 1;
3. стратегия, которой этот источник не по зубам (``raw-md`` на HTML, ``trafilatura``
   на markdown) → ``'strategy'``: источник получен, дело в разборе, краснеет шаг 2;
4. провал попадает в ``last_error.kind`` (виден после перезагрузки панели);
5. ``/rerun`` отдаёт ``failure_kind`` наружу — панели есть на что опереться.

Запуск: python tools/tests/test_failure_kind.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from serve import failure_kind  # noqa: E402

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    print(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f" — {note or detail}" if (note or detail) else ""))


def main() -> int:
    print("1. источник не получен = шаг 1 (краснеет блок «Источник»)")
    check("в поле «1» (владелец) → source", failure_kind("1", "auto", {}) == "source",
          failure_kind("1", "auto", {}))
    check("пустое поле → source", failure_kind("", "auto", {}) == "source",
          failure_kind("", "auto", {}))
    check("несуществующий локальный путь → source",
          failure_kind("D:/нет/такого.pdf", "auto", {}) == "source")
    check("URL, который не отдал текст (ни одна стратегия) → source",
          failure_kind("https://example.com/blog/", "auto", {}) == "source")
    check("сеть недоступна у каскада → source",
          failure_kind("https://example.com/a.html", "html-cascade", {}) == "source")

    print("\n2. источник есть, а разбор ему не подходит = шаг 2 (краснеет блок «Анализ»)")
    # Файлы заводим настоящие: признак шага зависит от того, существует ли источник,
    # и подделка пути («D:/docs/guide.markdown») проверяла бы выдумку, а не код.
    tmp = Path(tempfile.mkdtemp(prefix="b2s_kind_"))
    md_file = tmp / "readme.md"
    md_file.write_text("# заголовок\n", encoding="utf-8")
    MD_upper = tmp / "GUIDE.MD"
    MD_upper.write_text("# заголовок\n", encoding="utf-8")
    html_file = tmp / "page.html"
    html_file.write_text("<html><body>t</body></html>", encoding="utf-8")
    check("raw-md на HTML-файл → strategy",
          failure_kind(str(html_file), "raw-md", {}) == "strategy",
          failure_kind(str(html_file), "raw-md", {}))
    check("trafilatura на markdown-файл → strategy",
          failure_kind(str(md_file), "trafilatura", {}) == "strategy",
          failure_kind(str(md_file), "trafilatura", {}))
    check("bs4 на markdown-файл → strategy",
          failure_kind(str(md_file), "bs4", {}) == "strategy")
    check("регистр расширения не решает (.MD) → strategy для bs4",
          failure_kind(str(MD_upper), "bs4", {}) == "strategy")

    print("\n3. пары «источник подходит стратегии» остаются шагом 1, а не разбором")
    check("raw-md на .md → source (стратегия подходящая, дело в доступе)",
          failure_kind(str(md_file), "raw-md", {}) == "source")
    check("trafilatura на HTML-файл → source",
          failure_kind(str(html_file), "trafilatura", {}) == "source")
    check("URL + raw-md → strategy (источник заведомо есть, разбор ему не тот)",
          failure_kind("https://example.com/a.html", "raw-md", {}) == "strategy")
    check("«1» даже при raw-md → source: это не источник, а строка в поле",
          failure_kind("1", "raw-md", {}) == "source",
          failure_kind("1", "raw-md", {}))
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n4. ядро кладёт признак в state и в ответ /rerun")
    serve_src = (REPO / "tools" / "serve.py").read_text(encoding="utf-8", errors="replace")
    api_src = (REPO / "tools" / "api.py").read_text(encoding="utf-8", errors="replace")
    check("last_error несёт kind (видно после перезагрузки панели)",
          '"kind": failure_kind(src, strat, report)' in serve_src)
    check("/rerun отдаёт failure_kind наружу",
          '"failure_kind": None if ok else failure_kind(' in api_src)
    check("api.py импортирует failure_kind из serve (не копия логики)",
          "failure_kind," in api_src and "from serve import" in api_src)

    bad = [n for n, ok in checks if not ok]
    print(f"\nпроверок: {len(checks)}, провалов: {len(bad)}")
    if bad:
        print("провалено: " + "; ".join(bad))
        return 1
    print("ВСЁ ЗЕЛЁНОЕ: провал адресован своему шагу")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
