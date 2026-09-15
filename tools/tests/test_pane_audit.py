#!/usr/bin/env python3
"""Аудит связей внутри категории: кнопка в панели, маршрут, ядро, запись связей.

Зачем: связи между скиллами категории живут именами в бэктиках, и когда скиллов
становится много, граф расходится с тем, что записано в шапках (`related_skills`).
Инструмент аудита теперь часть проекта: скрипт лежит в `tools/audit/links.py`, ядро даёт
команду `audit-links`, а панель - кнопку 🔗 рядом с урной с окном подтверждения.

Что проверяем
-------------
1. Кнопка аудита живёт в шапке рядом с урной и открывает ОКНО, а не запускает работу;
2. в окне видно текущую категорию и предупреждение, что граф строится ТОЛЬКО по ней;
3. два действия: «Проверить» (чтение) и «Обновить связи» (запись related_skills);
4. ядро: маршрут `/audit_links`, CLI `audit-links`, загрузка модуля `tools/audit/links.py`;
5. живая проверка ядра: граф считается, `--apply` переписывает `related_skills`
   по факту графа и НЕ выходит за пределы категории.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
PLUGIN = REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js"
API = REPO / "hermes" / "plugins" / "b2s" / "dashboard" / "plugin_api.py"
CORE = REPO / "tools" / "api.py"
LINKS = REPO / "tools" / "audit" / "links.py"
HOME = pathlib.Path("D:/tmp/b2s-fakehome-audit")

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f" - {detail}" if detail else ""))


def main() -> int:
    src = PLUGIN.read_text(encoding="utf-8")
    core = CORE.read_text(encoding="utf-8")
    api = API.read_text(encoding="utf-8")

    # 1. кнопка рядом с урной, открывает окно
    check("кнопка аудита есть в шапке и несёт подсказку",
          "fitLabel('🔗', TIP.auditLinks)" in src)
    check("клик открывает ОКНО, а не запускает аудит сразу",
          "onClick: () => { setAuditRep(null); setAuditOpen(true) }" in src)
    check("связи ЛЕВЕЕ ведра, ведро - крайняя правая кнопка",
          src.index("fitLabel('🔗', TIP.auditLinks)") < src.index("fitLabel('🗑️', TIP.purgeAll)"))
    check("обе кнопки в одной группе и впритык (gap-1, как у кнопок блока 1)",
          "className: 'flex shrink-0 items-center gap-1'" in src,
          "владелец: «не на расстоянии пушечного выстрела» - разводить их justify-between нельзя")

    # 2. окно: категория + предупреждение
    i_open = src.find("Окно аудита связей")
    window = src[i_open:i_open + 4000] if i_open > 0 else ""
    check("в окне названа текущая категория",
          "children: 'Категория: ' + ((cat || '').trim()" in window)
    check("в окне сказано, что граф строится ТОЛЬКО по этой категории",
          "Граф строится ТОЛЬКО по этой категории" in window)
    check("в окне сказано, что связи обновятся только внутри категории",
          "тоже только внутри неё" in window and "не удаляются и не переименовываются" in window)

    # 3. два действия: чтение и запись
    check("действие «Проверить» - только чтение (apply=false)",
          "onClick: () => runAudit(false)" in src)
    check("действие «Обновить связи» пишет (apply=true)",
          "onClick: () => runAudit(true)" in src)
    check("панель зовёт именно /audit_links и передаёт категорию",
          "ctx.rest('/audit_links'" in src and "body: { cat, apply: !!apply }" in src)

    # 4. ядро и маршрут
    check("маршрут /audit_links объявлен и зовёт do_audit_links",
          '@router.post("/audit_links")' in api and "do_audit_links(body.cat, body.apply)" in api)
    check("ядро умеет audit-links и грузит модуль tools/audit/links.py",
          'sub.add_parser("audit-links"' in core and '"audit" / "links.py"' in core)
    check("модуль аудита лежит в репозитории (не только в профиле)",
          LINKS.is_file() and LINKS.stat().st_size > 2000, str(LINKS))

    # 5. живая проверка: песочница с двумя скиллами
    if HOME.exists():
        shutil.rmtree(HOME, ignore_errors=True)
    cat = HOME / "skills" / "probe-cat"
    for name, body in (
        ("alpha", "нужен `beta` — и только он\n"),
        ("beta", "сам никого не зовёт\n"),
    ):
        d = cat / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            "---\nname: " + name + "\ndescription: Use when probing.\nmetadata:\n  hermes:\n"
            "    tags: [\"probe\"]\n---\n\n" + body, encoding="utf-8", newline="\n")

    env = {"HERMES_HOME": str(HOME), "PATH": "/usr/bin:/bin"}
    proc = subprocess.run([sys.executable, str(CORE), "audit-links", "--cat", "probe-cat", "--apply"],
                          capture_output=True, text=True, env=env, cwd=str(REPO))
    try:
        out = json.loads(proc.stdout or "{}")
    except Exception:  # noqa: BLE001
        out = {}
    check("ядро посчитало граф в песочнице",
          out.get("ok") is True and out.get("count") == 2 and out.get("edge_count") == 1,
          str(out)[:160])
    check("граф верно назвал сироту (alpha) и молчуна (beta)",
          out.get("orphans") == ["alpha"] and out.get("silent") == ["beta"]
          and out.get("outgoing", {}).get("alpha") == ["beta"],
          json.dumps({k: out.get(k) for k in ("orphans", "silent", "outgoing")}, ensure_ascii=False))
    applied = out.get("apply") or {}
    check("related_skills записаны по факту графа",
          applied.get("updated") == ["alpha"], json.dumps(applied, ensure_ascii=False))
    alpha = (cat / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    check("в шапке alpha появилась строка related_skills: [beta]",
          "related_skills: [beta]" in alpha)
    check("запись не вышла за пределы категории (соседних каталогов нет)",
          not (HOME / "skills" / "alpha").exists() and not (HOME / "skills" / "beta").exists()
          and (cat / "beta" / "SKILL.md").is_file(), "появились каталоги вне категории")
    shutil.rmtree(HOME, ignore_errors=True)

    bad = [n for n, ok in checks if not ok]
    print(f"\nпроверок: {len(checks)}, провалов: {len(bad)}")
    if bad:
        print("провалено: " + "; ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())