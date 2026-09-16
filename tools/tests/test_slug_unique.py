#!/usr/bin/env python3
"""Сторож слага источника: длинные адреса больше не сливаются в один ключ.

Зачем. Слаг адреса - это и имя файла в ``b2s_fetched``, и ключ каталога в ``staging``.
До фикса он просто обрезался до 80 знаков, а у длинных ссылок r_keeper номер страницы
стоит в ХВОСТЕ - значит два разных адреса с общим началом давали один слаг, один файл
сырья и один каталог черновика. Владелец и поймал это: «каким макаром новый url по
метрикам сходится с файлом анализа старого url?».

Правила, которые сторожим:
1. короткий адрес - слаг КАК БЫЛ (никакого хэша): ключи существующих черновиков живы;
2. длинный адрес - к обрезанному слагу добавлен короткий хэш адреса, длина <= 80;
3. два РАЗНЫХ длинных адреса с общим началом - РАЗНЫЕ слаги;
4. если по старой (обрезанной) форме в каталоге-хранителе уже что-то лежит, возвращается
   она: ключ, однажды выданный, не меняется, иначе готовый черновик станет невидимым.

Проверки
--------
1-2. короткий/длинный: форма и длина;
3.   различие двух длинных адресов (главный смысл фикса);
4.   сохранение выданного ключа (keep_dirs);
5.   живой случай из проекта: файл сырья по прежнему ключу и его stem совпадают.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from book_to_skill.fetcher import SLUG_MAX, slug_for_url  # noqa: E402

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    print(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f" - {note or detail}" if (note or detail) else ""))


SHORT = "https://example.com/guide.html"
LONG_A = "https://docs.rkeeper.ru/rk7/latest/ru/alcohol-accounting-interface-for-external-services-rkalcoex-dll-140051474.html"
LONG_B = "https://docs.rkeeper.ru/rk7/latest/ru/alcohol-accounting-interface-for-external-services-rkalcoex-dll-140051475.html"
LEGACY_A = ("docs-rkeeper-ru-rk7-latest-ru-alcohol-accounting-interface-for-external-services")

s_short = slug_for_url(SHORT)
s_a = slug_for_url(LONG_A)
s_b = slug_for_url(LONG_B)

check("короткий адрес сохраняет прежнюю форму слага",
      s_short == "example-com-guide-html",
      f"получилось {s_short!r}: хэш полез в короткие адреса и сломал выданные ключи")

check("длинный адрес обрезан и помечен хэшем адреса",
      len(s_a) <= SLUG_MAX and re.search(r"-[0-9a-f]{8}$", s_a) is not None,
      f"{s_a!r} (длина {len(s_a)})")

check("два РАЗНЫХ длинных адреса дают РАЗНЫЕ слаги",
      s_a != s_b,
      "адреса слились в один слаг: файл сырья и каталог черновика будут общими")

check("слаг обрезан до общего предела (80)",
      len(s_b) <= SLUG_MAX, f"длина {len(s_b)}")

# Сохранение выданного ключа: подкладываем файл по СТАРОЙ форме в каталог-хранитель.
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    keep = Path(tmp)
    # Файл сырья хранит в шапке свой URL - по нему и узнаём законного владельца ключа.
    (keep / (LEGACY_A + ".md")).write_text(f"URL: {LONG_A}\n\nтекст\n", encoding="utf-8")
    s_kept = slug_for_url(LONG_A, keep_dirs=(keep,))
    check("выданный ключ сохраняется (файл сырья по старой форме)",
          s_kept == LEGACY_A,
          f"получилось {s_kept!r}: черновик потерял бы свой каталог")
    s_new = slug_for_url(LONG_B, keep_dirs=(keep,))
    check("чужой адрес с тем же началом НЕ забирает выданный ключ",
          s_new != LEGACY_A and s_new != s_kept,
          f"получилось {s_new!r}: ключ достался не своему источнику - файлы схлопнулись")

# Живой случай проекта: файл сырья, по которому сейчас работает панель.
FETCH = REPO / "b2s_fetched"
if FETCH.is_dir():
    stems = {p.stem for p in FETCH.glob("*.md")}
    checks_live = [st for st in stems if len(st) >= SLUG_MAX]
    ok_live = bool(checks_live) and all(
        slug_for_url(LONG_A if st == LEGACY_A else LONG_A, keep_dirs=(FETCH,)) in stems
        for st in checks_live[:1])
    check("файл сырья на диске адресуется своим прежним ключом",
          ok_live,
          f"в b2s_fetched: {sorted(stems)[:3]}",
          note=f"проверен ключ {LEGACY_A}")

print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
if any(not ok for _, ok in checks):
    print("провалено: " + "; ".join(name for name, ok in checks if not ok))
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ: слаг различает длинные адреса и не ломает уже выданные ключи")
