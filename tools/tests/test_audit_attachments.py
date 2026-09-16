#!/usr/bin/env python3
"""Сторож аудита вложений: архивы в скилле против их описания в SKILL.md.

Зачем. Кнопка «Проверить связи скиллов» теперь начинается с ЭТОГО шага: агент узнаёт о
вложениях только из текста `SKILL.md` (хост список файлов не отдаёт), поэтому архив без
описания - файл, которого для агента не существует. Сторож держит оба конца:

* статусы: `missing` (нет раздела), `partial` (раздел есть, архив не назван),
  `ok-unlisted` (назван, но часть файлов из архива не упомянута), `ok`, `broken`;
* `apply` ДОПИСЫВАЕТ раздел там, где его нет, и НЕ ТРОГАЕТ существующий (в нём смысл,
  который писал человек или агент);
* порядок шагов в ядре: вложения идут ПЕРЕД графом перекрёстных ссылок.

Проверки
--------
1. скилл без архивов - `status: none`;
2. архив без раздела - `missing`, в сводке `need_guide`;
3. архив назван, файлы описаны - `ok`;
4. часть файлов архива не упомянута - `ok-unlisted` со списком;
5. архив битый - `broken`, а не падение;
6. `apply_guide` дописывает раздел ТОЛЬКО там, где его нет;
7. в ядре `do_audit_links` блок `attachments` идёт до графа ссылок.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import sys
import tempfile
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "b2s_audit_attachments", REPO / "tools" / "audit" / "attachments.py")
att = importlib.util.module_from_spec(spec)
spec.loader.exec_module(att)

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    print(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f" - {note or detail}" if (note or detail) else ""))


def make_skill(root: pathlib.Path, name: str, text: str, files: dict | None = None) -> pathlib.Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8", newline="\n")
    if files:
        zp = d / "assets" / "probe.zip"
        zp.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zp, "w") as z:
            for fn, body in files.items():
                z.writestr(fn, body)
    return d


with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)

    # 1. Скилл без архивов.
    make_skill(root, "no-zips", "# Скилл\n\nБез вложений.\n")
    rep = att.scan(root)
    row = next(s for s in [att.check_skill(root / "no-zips")])
    check("скилл без архивов - статус none",
          row["status"] == "none" and row["zips_count"] == 0,
          f"статус {row['status']!r}, архивов {row['zips_count']}")

    # 2. Архив без раздела про вложения.
    make_skill(root, "with-zip-undescribed", "# Скилл\n\nТут просто текст.\n",
               {"run.bat": "echo hi", "notes.txt": "заметки"})
    rep = att.scan(root)
    check("архив без раздела вложений - статус missing",
          rep["need_guide"] == ["with-zip-undescribed"] and
          rep["details"][0]["status"] == "missing",
          f"need_guide={rep['need_guide']}, статус {rep['details'][0]['status']!r}")

    # 3. Архив назван, файлы описаны - ok.
    make_skill(root, "with-zip-described",
               "# Скилл\n\n## Файлы скилла (вложения)\n\n"
               "| Файл | Что это |\n|---|---|\n| `assets/probe.zip` | пример |\n\n"
               "Внутри: `run.bat` и `notes.txt`.\n",
               {"run.bat": "echo hi", "notes.txt": "заметки"})
    rep = att.scan(root)
    row = next(s for s in rep["details"] if s["skill"] == "with-zip-described")
    check("архив назван и файлы описаны - статус ok",
          row["status"] == "ok" and row["zips"][0]["unlisted"] == [],
          f"статус {row['status']!r}, неупомянуто {row['zips'][0]['unlisted']}")

    # 4. Часть файлов архива не упомянута.
    make_skill(root, "with-zip-partly",
               "# Скилл\n\n## Файлы скилла (вложения)\n\n| `assets/probe.zip` | пример |\n\n"
               "Внутри только `run.bat`.\n",
               {"run.bat": "echo hi", "silent.txt": "молчун"})
    rep = att.scan(root)
    row = next(s for s in rep["details"] if s["skill"] == "with-zip-partly")
    check("неупомянутый файл архива виден как ok-unlisted",
          row["status"] == "ok-unlisted" and row["zips"][0]["unlisted"] == ["silent.txt"],
          f"статус {row['status']!r}, неупомянуто {row['zips'][0]['unlisted']}")

    # 5. Битый архив не роняет проверку.
    bad = root / "with-bad-zip"
    bad.mkdir()
    (bad / "SKILL.md").write_text("# Скилл\n", encoding="utf-8")
    (bad / "assets").mkdir()
    (bad / "assets" / "broken.zip").write_bytes(b"not a zip at all")
    rep = att.scan(root)
    row = next(s for s in rep["details"] if s["skill"] == "with-bad-zip")
    check("битый архив - статус broken, без падения",
          row["status"] == "broken" and rep["broken"],
          f"статус {row['status']!r}, broken={len(rep['broken'])}")

    # 6. apply дописывает раздел только там, где его нет.
    before = (root / "with-zip-described" / "SKILL.md").read_text(encoding="utf-8")
    res = att.apply_guide(root)
    after = (root / "with-zip-described" / "SKILL.md").read_text(encoding="utf-8")
    fresh = (root / "with-zip-undescribed" / "SKILL.md").read_text(encoding="utf-8")
    check("apply дописал раздел там, где его не было",
          "with-zip-undescribed" in res["written"] and att.GUIDE_TITLE in fresh,
          f"written={res['written']}")
    check("битому архиву раздел НЕ дописан (сначала чинить архив)",
          res["broken_skipped"] == ["with-bad-zip"],
          f"broken_skipped={res['broken_skipped']}")
    check("apply НЕ тронул существующий раздел",
          "with-zip-described" in res["kept"] and before == after,
          "существующий раздел перезаписан - потерян смысл, который писал автор")

# 7. Порядок шагов в ядре: вложения проверяются до графа ссылок.
api_src = (REPO / "tools" / "api.py").read_text(encoding="utf-8", errors="replace")


def live_pos(needle: str) -> int:
    """Позиция ЖИВОЙ строки с вызовом: строка внутри комментария не считается.

    Иначе сторож обманывается сам: закомментированный вызов `att.scan(root)` остаётся
    в тексте файла и «порядок» выглядит целым, хотя шаг больше не выполняется.
    """
    pos = api_src.find(needle)
    while pos > 0:
        start = api_src.rfind("\n", 0, pos) + 1
        # Живой вызов - если перед ним в строке нет `#` (иначе это комментарий, пусть даже
        # после кода: `pass  # att.scan(root)`).
        if "#" not in api_src[start:pos]:
            return pos
        pos = api_src.find(needle, pos + 1)
    return -1


i_att = live_pos("attachments = att.scan(root)")
i_graph = live_pos("report = module.build_report(root)")
check("в ядре вложения идут ПЕРВЫМ шагом, граф ссылок - вторым",
      i_att > 0 and i_graph > i_att,
      f"attachments@{i_att}, граф@{i_graph}")
check("шаги подписаны в ядре (ШАГ 1 / ШАГ 2)",
      "# ШАГ 1" in api_src and "# ШАГ 2" in api_src,
      "в коде нет пометок порядка - следующий читатель их не увидит")

# 8. Панель: в окне аудита вложения идут первым блоком результата.
pane = (REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js").read_text(
    encoding="utf-8", errors="replace")
i_pane_att = pane.find("'ВЛОЖЕНИЯ: архивов '")
i_pane_graph = pane.find("'ГРАФ СВЯЗЕЙ: скиллов: '")
check("в окне аудита блок вложений стоит ПЕРЕД графом",
      i_pane_att > 0 and i_pane_graph > i_pane_att,
      f"вложения@{i_pane_att}, граф@{i_pane_graph}")
check("окно аудита называет порядок шагов словами", "СНАЧАЛА вложения" in pane,
      "владелец не узнает, что проверка начинается с архивов")
check("«Обновить связи» обещает дополнить разделы вложений",
      "дополнить разделы вложений" in pane,
      "кнопка молчит про вторую часть своей работы")

print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
if any(not ok for _, ok in checks):
    print("провалено: " + "; ".join(n for n, ok in checks if not ok))
    sys.exit(1)
print("ВСЁ ЗЕЛЁНОЕ: вложения проверяются первыми и их описание держится под сторожем")
