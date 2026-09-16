#!/usr/bin/env python3
"""Сторож ключа источника: имя файла = читаемый слаг + хэш ПОЛНОГО адреса.

Зачем. Ключ источника - это имя файла в ``b2s_fetched`` и имя каталога черновика в
``staging``. Схема «обрезать слаг до 80 знаков» делала РАЗНЫЕ адреса одним ключом: у
страниц r_keeper различие стоит в хвосте (номер), а у коротких - в схеме, регистре или
query. Владелец: «в чём проблема отличить два url, у которых отличается один знак?».

Правила, которые сторожим:
1. ключ = читаемая часть + '-' + 10 знаков sha1 ПОЛНОГО адреса;
2. адреса, различающиеся схемой, регистром или query, дают РАЗНЫЕ ключи;
3. одинаковый адрес всегда даёт ОДИН и тот же ключ (стабильность ключа);
4. длина ключа <= 80: режется только читаемая часть, хэш не режется никогда;
5. старый (выданный ранее) ключ возвращается только СВОЕМУ адресу - по URL в шапке файла
   сырья; чужой адрес с тем же началом его не забирает.

Проверки
--------
1-3. форма, различия (схема/регистр/query), стабильность;
4.   предел длины;
5-6. выдача ключа владельцу и защита от чужих;
7.   живой ключ проекта: файл сырья, по которому работает панель.
"""
from __future__ import annotations

import re
import sys
import tempfile
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
LEGACY_A = "docs-rkeeper-ru-rk7-latest-ru-alcohol-accounting-interface-for-external-services"

s_short = slug_for_url(SHORT)
s_a = slug_for_url(LONG_A)
s_b = slug_for_url(LONG_B)

check("ключ = читаемая часть + хэш полного адреса",
      re.search(r"-[0-9a-f]{10}$", s_short) is not None
      and s_short.startswith("example-com-guide-html"),
      f"получилось {s_short!r}")

check("два длинных адреса с разницей в хвосте дают РАЗНЫЕ ключи",
      s_a != s_b, f"A={s_a!r} B={s_b!r}")

for name, u1, u2 in (("схема", "http://example.com/a", "https://example.com/a"),
                     ("регистр пути", "https://example.com/a", "https://example.com/A"),
                     ("query", "https://example.com/page?id=1", "https://example.com/page?id=2")):
    check(f"адреса, различающиеся только на {name}, дают разные ключи",
          slug_for_url(u1) != slug_for_url(u2),
          f"{slug_for_url(u1)!r} против {slug_for_url(u2)!r}")

check("один и тот же адрес даёт ОДИН ключ (стабильность)",
      slug_for_url(LONG_A) == s_a and len({slug_for_url(LONG_A) for _ in range(5)}) == 1,
      "ключ плавает между вызовами: панель не найдёт свой файл")

check("длина ключа не превышает 80",
      max(len(slug_for_url(u)) for u in (SHORT, LONG_A, LONG_B)) <= SLUG_MAX,
      f"длины: {[len(slug_for_url(u)) for u in (SHORT, LONG_A, LONG_B)]}")

with tempfile.TemporaryDirectory() as tmp:
    keep = Path(tmp)
    # Файл сырья хранит в шапке свой URL - по нему узнаём законного владельца ключа.
    (keep / (LEGACY_A + ".md")).write_text(f"URL: {LONG_A}\n\nтекст\n", encoding="utf-8")
    s_kept = slug_for_url(LONG_A, keep_dirs=(keep,))
    check("выданный ранее ключ возвращается своему владельцу",
          s_kept == LEGACY_A, f"получилось {s_kept!r}: черновик потерял бы каталог")
    s_new = slug_for_url(LONG_B, keep_dirs=(keep,))
    check("чужой адрес с тем же началом НЕ забирает выданный ключ",
          s_new != LEGACY_A, f"получилось {s_new!r}: файлы разных источников схлопнулись")

FETCH = REPO / "b2s_fetched"
if FETCH.is_dir():
    stems = {p.stem for p in FETCH.glob("*.md")}
    check("файл сырья на диске адресуется своим прежним ключом",
          LEGACY_A in stems and slug_for_url(LONG_A, keep_dirs=(FETCH,)) == LEGACY_A,
          f"в b2s_fetched нет {LEGACY_A!r}", note=LEGACY_A)

print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
if any(not ok for _, ok in checks):
    print("провалено: " + "; ".join(name for name, ok in checks if not ok))
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ: ключ источника различает адреса и не ломает уже выданные")
