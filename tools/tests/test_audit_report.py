#!/usr/bin/env python3
"""Сторож отчёта аудита: АУДИТ-СВЯЗЕЙ.md в каталоге категории + автооткрытие в панели.

Зачем. Кнопка «Отчёт в файл» делает тот же аудит, но результат обязан лечь ФАЙЛОМ
рядом со скиллами (владелец читает его и планирует добор, а не ищет по диску), а
панель - открыть этот файл системным приложением, связанным с ``.md``. Сторож держит
четыре конца:

* файл создаётся в каталоге КАТЕГОРИИ и перезаписывается ЦЕЛИКОМ, а не дописывается;
* отчёт не трогает скиллы: ни один файл категории не меняется;
* раздел «страницы документации»: сверка по id с допуском ±1 (в метаданных бывает
  адрес версии страницы), честный источник ``rest`` / ``cache`` / ``none``;
* панель: кнопка зовёт ``/audit_report``, гаснет на время прогона и открывает файл
  через ``ctx.os.openExternal`` с запасным ``revealPath``.

Проверок: 13.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

spec = importlib.util.spec_from_file_location("b2s_api", REPO / "tools" / "api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)

PANEL = REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js"
ROUTE = REPO / "hermes" / "plugins" / "b2s" / "dashboard" / "plugin_api.py"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f" - {detail}" if detail and not ok else ""))


def sha_tree(root: pathlib.Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return out


with tempfile.TemporaryDirectory() as tmp_dir:
    tmp = pathlib.Path(tmp_dir)
    home = tmp / "home"
    cat = home / "skills" / "rk7xml-interface"
    (cat / "alpha").mkdir(parents=True)
    (cat / "beta").mkdir(parents=True)

    (cat / "alpha" / "SKILL.md").write_text(
        "# alpha\n\nСосед по теме: `beta`.\n", encoding="utf-8", newline="\n")
    (cat / "beta" / "SKILL.md").write_text(
        "# beta\n\nМатериал без ссылок на соседей.\n", encoding="utf-8", newline="\n")
    # alpha основан на ВЕРСИИ страницы: id на 1 меньше самой страницы (140051475).
    (cat / "alpha" / "metadata.json").write_text(
        json.dumps({"source": {"url": "https://docs.rkeeper.ru/rk7/latest/ru/alko-140051474.html"}},
                   ensure_ascii=False), encoding="utf-8", newline="\n")
    (cat / "beta" / "metadata.json").write_text(
        json.dumps({"source": {"url": "https://docs.rkeeper.ru/rk7/latest/ru/drugoe-111111111.html"}},
                   ensure_ascii=False), encoding="utf-8", newline="\n")

    api.hermes_home = lambda: home          # профиль - песочница, а не диск владельца
    api.AUDIT_CACHE_DIR = tmp / "cache"     # кэш портала тоже в песочницу

    report = cat / "АУДИТ-СВЯЗЕЙ.md"
    out = api.do_audit_report("rk7xml-interface", online=False)

    check("отчёт создан в каталоге категории",
          bool(out.get("ok")) and pathlib.Path(str(out.get("path"))) == report and report.is_file(),
          f"ok={out.get('ok')}, path={out.get('path')}, error={out.get('error')}")

    text = report.read_text(encoding="utf-8") if report.is_file() else ""
    heads = ("# Аудит связей категории: rk7xml-interface", "## 1. Сводка", "## 2. Вложения",
             "## 3. Граф перекрёстных ссылок", "## 4. Карта ссылок",
             "## 5. Страницы документации", "## 6. Что предлагаю")
    check("разделы 1-6 на месте", all(h in text for h in heads),
          f"нет: {[h for h in heads if h not in text]}")

    check("файл в LF, без CRLF",
          report.is_file() and b"\r" not in report.read_bytes())

    check("молчун назван в отчёте, связь alpha -> beta в карте",
          "`beta`" in text and "Молчуны" in text and "`alpha`" in text,
          "граф в отчёте не описан")

    before = sha_tree(cat)
    before.pop("АУДИТ-СВЯЗЕЙ.md", None)
    with report.open("a", encoding="utf-8") as fh:
        fh.write("\nМУСОР ОТ ПРОШЛОГО ПРОГОНА\n")
    second = api.do_audit_report("rk7xml-interface", online=False)
    text2 = report.read_text(encoding="utf-8")
    check("отчёт перезаписывается целиком",
          "МУСОР" not in text2 and text2.count("# Аудит связей категории") == 1,
          f"заголовков: {text2.count('# Аудит связей категории')}")

    after = sha_tree(cat)
    after.pop("АУДИТ-СВЯЗЕЙ.md", None)
    changed = [k for k in after if before.get(k) != after[k]]
    check("скиллы не тронуты: ни одного изменённого файла",
          not changed and bool(second.get("ok")), f"изменилось: {changed}")

    pages = api._load_audit_pages()
    cache = api.AUDIT_CACHE_DIR
    pages.save_cache(cache, "19605640", [
        {"id": "140051475", "title": "Alcohol accounting interface",
         "webui": "/rk7/latest/ru/alko-140051475.html", "depth": 1, "parent": "19605640"},
        {"id": "999999999", "title": "Не разобранная страница",
         "webui": "/rk7/latest/ru/new-999999999.html", "depth": 1, "parent": "19605640"},
    ])
    docs = pages.scan(cat, "19605640", cache, online=False)
    check("источник списка - кэш, с датой снимка",
          docs.get("source") == "cache" and bool(docs.get("fetched_at")),
          f"source={docs.get('source')}, fetched_at={docs.get('fetched_at')!r}")
    check("адрес версии страницы (id±1) считается закрытым скиллом",
          any(c.get("skill") == "alpha" for c in docs.get("covered") or []),
          str(docs.get("covered")))
    check("неразобранная страница попадает в добор",
          any(m.get("id") == "999999999" for m in docs.get("missing") or []),
          str(docs.get("missing")))

    # Офлайн без кэша: раздел честно говорит, что списка нет, а не молчит.
    api.AUDIT_CACHE_DIR = tmp / "empty-cache"
    out_off = api.do_audit_report("rk7xml-interface", online=False)
    text_off = report.read_text(encoding="utf-8")
    check("без сети и без кэша отчёт честно помечает источник",
          bool(out_off.get("ok")) and out_off.get("docs_source") == "none"
          and "получить не удалось" in text_off,
          f"docs_source={out_off.get('docs_source')}")

    out_bad = api.do_audit_report("net-takoj-kategorii", online=False)
    check("чужая категория: отказ и файла нет",
          not out_bad.get("ok") and not (home / "skills" / "net-takoj-kategorii").exists(),
          str(out_bad.get("error")))

panel = PANEL.read_text(encoding="utf-8", errors="replace")
check("панель: кнопка «Отчёт в файл» зовёт /audit_report",
      "'Отчёт в файл'" in panel and "'/audit_report'" in panel)
check("панель: файл открывается системно, с запасным путём",
      "ctx.os.openExternal" in panel and "ctx.os.revealPath" in panel
      and "auditBusy === 'report'" in panel,
      "нет открытия файла или замка кнопки")

route = ROUTE.read_text(encoding="utf-8", errors="replace")
check("роут /audit_report объявлен в plugin_api",
      '@router.post("/audit_report")' in route and "do_audit_report" in route)

bad = [name for name, ok in checks if not ok]
print(f"\nпроверок: {len(checks)}, провалов: {len(bad)}")
if bad:
    print("провалы: " + "; ".join(bad))
    sys.exit(1)
