"""trafilatura печатает markdown-таблицы без строки-разделителя.

Без разделителя Markdown таблицу не видит: в панели это простыня с палками, а
глоссарий, собранный по таблице, даёт 0 терминов. Проверяем вставку разделителя,
нетронутость уже корректных таблиц и то, что конвейер `clean_html` реально
пропускает через него вывод извлекателя.
Run: python -B tools/tests/test_md_tables.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from book_to_skill.plugins.generic import clean_html, fix_md_tables  # noqa: E402

FAILS = []
CHECKS = 0


def check(name, got, want):
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILS.append(f"{name}\n  got:  {got!r}\n  want: {want!r}")


# 1. Таблица без разделителя (ровно то, что дал реальный Confluence-источник
#    docs.rkeeper.ru: заголовок и сразу строки данных) -> разделитель добавлен.
src = "\n".join([
    "## Список запросов",
    "",
    "| # | CMD | Доступность на | ",
    "| 1 | GetWaiterMessages | CS, ST | ",
    "| 2 | WaiterMessage | CS | ",
    "",
    "Текст ниже.",
])
want = "\n".join([
    "## Список запросов",
    "",
    "| # | CMD | Доступность на | ",
    "| --- | --- | --- |",
    "| 1 | GetWaiterMessages | CS, ST | ",
    "| 2 | WaiterMessage | CS | ",
    "",
    "Текст ниже.",
])
check("таблица 3 колонки без разделителя", fix_md_tables(src), want)

# 2. Уже корректная таблица не меняется.
check("корректная таблица не тронута", fix_md_tables(want), want)

# 3. Идемпотентность: второй прогон не добавляет второй разделитель.
check("идемпотентность", fix_md_tables(fix_md_tables(src)), want)

# 4. Короткий разделитель trafilatura (|---|---|) тоже признаём разделителем.
short = "| a | b |\n|---|---|\n| 1 | 2 |"
check("короткий разделитель не дублируется", fix_md_tables(short), short)

# 5. Одна строка с палками - не таблица, не трогаем.
check("одиночная строка", fix_md_tables("| просто | строка |"),
      "| просто | строка |")

# 6. Одна колонка - тоже не таблица (разделитель не из чего строить).
check("одна колонка", fix_md_tables("| a |\n| 1 |"), "| a |\n| 1 |")

# 7. Две колонки -> разделитель из двух ячеек.
check("две колонки",
      fix_md_tables("| a | b |\n| 1 | 2 |"),
      "| a | b |\n| --- | --- |\n| 1 | 2 |")

# 8. Два блока в тексте -> оба починены.
two = "| a | b |\n| 1 | 2 |\n\nтекст\n\n| c | d |\n| 3 | 4 |"
check("два блока",
      fix_md_tables(two),
      "| a | b |\n| --- | --- |\n| 1 | 2 |\n\nтекст\n\n| c | d |\n| --- | --- |\n| 3 | 4 |")

# 9. Пустые ячейки считаются колонками (в r_keeper так и есть).
check("пустые ячейки",
      fix_md_tables("| # | CMD | Доступность |\n| 1 | X | |"),
      "| # | CMD | Доступность |\n| --- | --- | --- |\n| 1 | X | |")

# 10. Текст без таблиц не меняется.
plain = "Просто абзац.\n\nИ ещё один."
check("без таблиц", fix_md_tables(plain), plain)

# 11. Палки внутри строки (не в начале) не считаются таблицей.
inline = "Флаг --table=a|b|c в тексте."
check("палки внутри строки", fix_md_tables(inline), inline)

# 12. Конвейер: что бы trafilatura ни вернула, разделитель на выходе есть.
#     Подменяем extract на «плохой» вывод (как на реальном источнике) - это
#     единственный способ проверить маршрут, не завися от версии trafilatura,
#     которая на простом HTML разделитель иногда ставит сама.
import trafilatura  # noqa: E402

_orig = trafilatura.extract
trafilatura.extract = lambda *a, **k: "| # | CMD |\n| 1 | X |\n| 2 | Y |"
try:
    text, winner, notes = clean_html("<html><body><p>x</p></body></html>")
finally:
    trafilatura.extract = _orig
check("конвейер: стратегия", winner, "trafilatura")
check("конвейер: разделитель в тексте", text,
      "| # | CMD |\n| --- | --- |\n| 1 | X |\n| 2 | Y |")
check("конвейер: пометка в notes",
      any("separator rows" in n for n in notes), True)

print(f"test_md_tables: {CHECKS} проверок, {len(FAILS)} провалов")
for f in FAILS:
    print("FAIL:", f)
sys.exit(1 if FAILS else 0)
