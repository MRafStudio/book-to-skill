#!/usr/bin/env python3
"""Локальный источник: путь на диске — не URL, а формат определяет апстрим.

Два дефекта, которые закрепляет этот набор:

1. Каскад слал локальный путь в сетевые стратегии. `raw-markdown` хватал любой
   `.md` и падал с `URLError: unknown url type: d` — панель показывала «болт»
   на файле, который лежит на том же диске, где работает Hermes. Сетевые плагины
   теперь отказываются от путей (`looks_like_path`), а путь берёт `local-file`.

2. Формат НЕ угадывается заново. В апстриме уже есть определение формата:
   расширение (`SUPPORTED_EXTENSIONS`), а при незнакомом — магия файла
   (`%PDF`, `PK` + `mimetype`/`word/document.xml`), плюс свой парсер на формат
   и `prepare_dependencies`. Плагин вызывает `extract_single_file` и не
   дублирует эту логику; свои читалки остаются только там, где апстрим слабее
   (не-utf8 кодировки).

Запуск: python tools/tests/test_local_source.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from book_to_skill.fetcher import Fetcher  # noqa: E402
from book_to_skill.plugins.generic import HtmlCascadePlugin, RawMarkdownPlugin  # noqa: E402
from book_to_skill.plugins.plugin_local_file import (  # noqa: E402
    LocalFilePlugin,
    looks_like_path,
)

TOTAL = 0
FAILED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global TOTAL, FAILED
    TOTAL += 1
    if cond:
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f"  -- {detail}" if detail else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="b2s-loc-"))
    lp = LocalFilePlugin()
    md = tmp / "note.md"
    md.write_text("# Заголовок\n\nтело\n", encoding="utf-8")
    txt = tmp / "plain.txt"
    txt.write_text("просто текст\n", encoding="utf-8")
    html = tmp / "page.html"
    html.write_text("<html><body><h1>Т</h1><p>абзац</p>"
                    "<script>var x=1</script></body></html>", encoding="utf-8")
    cp = tmp / "ru-cp1251.txt"
    cp.write_bytes("Привет, мир\n".encode("cp1251"))

    print("1. что вообще считается локальным путём")
    check("существующий .md → 1.0", lp.matches(str(md)) == 1.0, str(lp.matches(str(md))))
    check("несуществующий .md → 0.9 (сеть не при чём, ошибка будет внятной)",
          lp.matches(str(tmp / "nope.md")) == 0.9)
    check("http-URL → 0.0", lp.matches("https://docs.python.org/3/x.md") == 0.0)
    check("file:// → 0.0 (это URL, им занимается urllib)", lp.matches("file:///D:/tmp/a.md") == 0.0)
    check("просто текст без пути → 0.0", lp.matches("python pathlib") == 0.0)
    check("папка существует → 1.0", lp.matches(str(tmp)) == 1.0)
    check("Windows-путь с обратными слэшами распознан", looks_like_path("D:\\docs\\book.txt"))

    print("\n2. локальный путь не уходит в сеть")
    real_urlopen = urllib.request.urlopen

    def forbidden(*a, **k):
        raise AssertionError("сеть не должна вызываться для локального пути")

    urllib.request.urlopen = forbidden
    try:
        r_md, att_md = Fetcher().fetch(str(md))
    finally:
        urllib.request.urlopen = real_urlopen
    check("http-вызов не сделан (urlopen падал бы)", r_md is not None and r_md.ok, str(att_md))
    check("победила не сетевая стратегия",
          att_md and att_md[0]["plugin"] != "raw-markdown", str(att_md))
    check("сетевые стратегии больше не претендуют на путь",
          RawMarkdownPlugin().matches(str(md)) == 0.0
          and HtmlCascadePlugin().matches(str(html)) == 0.0)

    print("\n3. формат определяет апстрим, а не наш плагин")
    check("стратегия названа родным методом (upstream-*)",
          bool(r_md.strategy.startswith("upstream-")), r_md.strategy)
    check("формат назван родным экстрактором (md, а не 'markdown-fallback')",
          "формат определён как md" in " ".join(r_md.notes), str(r_md.notes))
    check("метод извлечения назван", any("метод извлечения" in n for n in r_md.notes), str(r_md.notes))
    check("текст совпадает с файлом (переводы строк нормализованы)",
          r_md.content.strip() == md.read_text(encoding="utf-8").strip(),
          repr(r_md.content[:40]))
    check("заголовки на месте (kind=markdown)", r_md.kind == "markdown" and r_md.content.startswith("# "))

    print("\n4. свои читалки только там, где апстрим слабее")
    r_cp, _ = Fetcher().fetch(str(cp))
    check("cp1251 прочитан нами (апстрим ушёл бы в cp1252 и дал кракозябры)",
          bool(r_cp) and r_cp.ok and "local-file" in r_cp.strategy, getattr(r_cp, "strategy", ""))
    check("текст cp1251 корректен", bool(r_cp) and "Привет, мир" in r_cp.content,
          repr(getattr(r_cp, "content", "")[:40]))
    check("кодировка названа в notes",
          bool(r_cp) and any("кодиров" in n.lower() for n in r_cp.notes), str(getattr(r_cp, "notes", [])))
    r_html, _ = Fetcher().fetch(str(html))
    check("локальный .html прочитан (родной html-парсер или наш clean_html)",
          bool(r_html) and r_html.ok, getattr(r_html, "error", ""))
    check("из .html вырезан <script>", bool(r_html) and "<script" not in r_html.content)
    r_txt, _ = Fetcher().fetch(str(txt))
    check(".txt прочитан", bool(r_txt) and r_txt.ok and "просто текст" in r_txt.content)

    print("\n5. каталог, отказ и отсутствующий путь говорят словами")
    sub = tmp / "book"
    sub.mkdir()
    (sub / "a.md").write_text("# a\n", encoding="utf-8")
    (sub / "b.md").write_text("# b\n", encoding="utf-8")
    r_dir, _ = Fetcher().fetch(str(sub))
    check("каталог собран в один источник", bool(r_dir) and r_dir.ok, str(r_dir))
    check("файлы идут по алфавиту (a до b)",
          r_dir.content.index("# a.md") < r_dir.content.index("# b.md"), r_dir.content[:60])
    empty = tmp / "empty"
    empty.mkdir()
    rep_empty = Fetcher().fetch_to_dir(str(empty), tmp / "out-empty")
    check("в каталоге нет .md — понятная ошибка",
          rep_empty["ok"] is False and "нет .md" in rep_empty["error"], rep_empty["error"])
    rep_ghost = Fetcher().fetch_to_dir(str(tmp / "ghost.md"), tmp / "out-ghost")
    check("отсутствующий файл: ok=False", rep_ghost["ok"] is False, str(rep_ghost))
    check("причина названа ('не найден'), а не 'no strategy produced text'",
          "не найден" in rep_ghost["error"], rep_ghost["error"])
    check("в попытках нет сетевого плагина (нечему падать с URLError)",
          all(a["plugin"] == "local-file" for a in rep_ghost["attempts"]),
          str([a["plugin"] for a in rep_ghost["attempts"]]))
    weird = tmp / "data.toml"
    weird.write_text("x = 1\n", encoding="utf-8")
    rep_weird = Fetcher().fetch_to_dir(str(weird), tmp / "out-weird")
    check("неподдерживаемый формат — отказ апстрима, а не молчание",
          rep_weird["ok"] is False and "Unsupported format" in rep_weird["error"],
          rep_weird["error"])

    print("\n6. регресс: сетевые URL по-прежнему у сетевых стратегий")
    check("local-file не матчит https", lp.matches("https://example.com/a.html") == 0.0)
    check("raw-markdown по-прежнему ловит .md по URL",
          RawMarkdownPlugin().matches("https://example.com/readme.md") == 0.95)
    check("html-cascade по URL не сломан",
          HtmlCascadePlugin().matches("https://example.com/a.html") == 0.5)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{TOTAL - FAILED}/{TOTAL} проверок")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
