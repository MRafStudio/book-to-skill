"""Проверка предложенного имени скилла (`tools/dashboard.py::suggest_skill_name`).

Имя скилла работает ТРИГГЕРОМ: по нему Hermes решает, когда скилл подгрузить, и
длинная слаг-простыня в этой роли бесполезна (владелец: «слишком длинное название -
максимум 3 слова»). Поэтому здесь проверяется ИНВАРИАНТ - слов не больше трёх, - и
он проверяется на широком наборе адресов: русские сегменты, предлоги, номера
страниц, версии, длинные хвосты. Конкретные примеры идут отдельно, как сторожа
привычных имён (`python-pathlib`, `microsoft-csharp-13`, `wikipedia-kubernetes`):
если лимит слов начнёт резать их, тест это покажет.

Функция чистая - ни сети, ни диска, ни профиля; песочница не нужна.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import dashboard as dash  # noqa: E402

failures: list[str] = []


def check(label: str, got, want) -> None:
    good = got == want
    print(f"  [{'ok ' if good else 'FAIL'}] {label}: {got!r}" + ("" if good else f" (ждали {want!r})"))
    if not good:
        failures.append(label)


def ok(label: str, cond: bool, note: str = "") -> None:
    print(f"  [{'ok ' if cond else 'FAIL'}] {label}{' - ' + note if note else ''}")
    if not cond:
        failures.append(label)


print("== привычные имена не сломались")
check("python pathlib",
      dash.suggest_skill_name("https://docs.python.org/3/library/pathlib.html",
                              "pathlib - Object-oriented filesystem paths"),
      "python-pathlib")
check("microsoft csharp-13 (версия в теме сохраняется)",
      dash.suggest_skill_name("https://learn.microsoft.com/en-us/dotnet/csharp/whats-new/csharp-13",
                              "What's new in C# 13"),
      "microsoft-csharp-13")
check("wikipedia kubernetes",
      dash.suggest_skill_name("https://en.wikipedia.org/wiki/Kubernetes", "Kubernetes"),
      "wikipedia-kubernetes")

print()
print("== русский источник: тема обрезается, предлоги и номер уходят")
long_url = ("https://docs.rkeeper.ru/rk7/latest/ru/"
            "rekomendatsii-po-polucheniyu-spravochnikov-iz-r_keeper-186253740.html")
name = dash.suggest_skill_name(long_url)
check("имя для rkeeper", name, "rkeeper-rekomendatsii-spravochnikov")
ok("слов ровно три", len(name.split("-")) == 3, " ".join(name.split("-")))
ok("начинается с сайта", name.startswith("rkeeper-"), name)
ok("предлоги не попали", "-po-" not in name and "-iz-" not in name, name)
ok("номер страницы не попал", "186253740" not in name, name)
ok("прежний длинный слаг не выдаём",
   name != "rkeeper-rekomendatsii-po-polucheniyu-spravochnik",
   "старое поведение резалось на 48 символах")

print()
print("== инвариант: не больше трёх слов, и это чистый слаг")
urls = [
    large for large in [
        long_url,
        "https://docs.rkeeper.ru/rk7/latest/ru/novaya-stranica-987654321.html",
        "https://learn.microsoft.com/ru-ru/dotnet/csharp/whats-new/csharp-13",
        "https://docs.python.org/3/library/pathlib.html",
        "https://en.wikipedia.org/wiki/Object-oriented_programming",
        "https://en.wikipedia.org/wiki/Kubernetes",
        "https://example.com/articles/how-to-make-a-very-long-article-name-for-testing",
        "https://example.com/a/b/c/d/e/f",
        "https://example.com/",
        "",
    ]
]
for url in urls:
    got = dash.suggest_skill_name(url)
    label = url or "(пустая строка)"
    ok(f"слов <= 3: {label[:70]}", len(got.split("-")) <= 3, got or "(пусто)")
    ok(f"слаг чистый: {label[:70]}",
       bool(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", got)) if got else True, got or "(пусто)")
    ok(f"длина <= 48: {label[:70]}", len(got) <= 48, str(len(got)))

print()
print("== фоллбэки: заголовок и имя файла")
check("тема из заголовка (url пуст)",
      dash.suggest_skill_name("", "My Great Book About Parsers"),
      "my-great-book")
check("тема из имени файла",
      dash.suggest_skill_name("", "", "/data/Some Long Manual Name.md"),
      "some-long-manual")

print()
if failures:
    print(f"ПРОВАЛЕНО проверок: {len(failures)}: {failures}")
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ")
