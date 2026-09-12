#!/usr/bin/env python3
"""Синхронизация зеркала ``hermes/`` (форк) ↔ ``$HERMES_HOME``.

Зачем
-----
Панель BOOK → SKILL живёт не в git-дереве пайплайна, а в профиле Hermes::

    $HERMES_HOME/desktop-plugins/b2s/plugin.js          # панель (rail)
    $HERMES_HOME/plugins/b2s/dashboard/plugin_api.py    # REST-маршруты
    $HERMES_HOME/plugins/b2s/dashboard/manifest.json    # api: true + enabled

Пока этих файлов нет в репозитории — правка панели не версионируется и
теряется при переносе профиля. Поэтому в форке держим зеркало ``hermes/``
той же структуры, а этот скрипт её разворачивает.

    python tools/sync_hermes.py            # форк → профиль (развернуть)
    python tools/sync_hermes.py --check    # только показать дрейф (exit 1, если есть)
    python tools/sync_hermes.py --pull     # профиль → форк (забрать правку в git)

``config.json`` в синхронизации НЕ участвует: он машинно-специфичный (пути к
клону и интерпретатору) и создаётся установщиком ``tools/install_plugin.py``.
В репозитории вместо него лежит шаблон ``config.example.json``.

После развёртывания ``plugin.js`` подхватывается хот-релоадом панели, а
``plugin_api.py`` — только рестартом dashboard (``tools/restart_dashboard.bat``):
роутеры монтируются на старте процесса.
"""
from __future__ import annotations

import argparse
import difflib
import filecmp
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIRROR = REPO / "hermes"
SKIP = {"__pycache__", ".DS_Store", "config.json", "config.example.json"}
# ↑ config.json — локальный для машины (пути к клону и интерпретатору), его пишет
#   tools/install_plugin.py; config.example.json — шаблон из репозитория.
#   Ни то, ни другое в профиль не копируем и из профиля не забираем.
# Синхронизируем только эти ветки зеркала: всё остальное (README и прочая
# документация) живёт в репозитории и в профиль не копируется.
MIRROR_ROOTS = ("desktop-plugins", "plugins")

# Файлы, требующие рестарта dashboard (изменения в них не видны на живом процессе)
NEEDS_RESTART = {"plugin_api.py", "manifest.json"}


def hermes_home(explicit: str = "") -> Path:
    """Где профиль Hermes: аргумент → env HERMES_HOME → типовые места установки.

    Общий детект живёт в ``tools/hermes_paths.py`` — тот же код нужен ядру и
    установщику плагина: путь этой машины в коде держать нельзя.
    """
    if explicit:
        return Path(explicit)
    sys.path.insert(0, str(REPO / "tools"))
    from hermes_paths import hermes_home as detect  # noqa: PLC0415 — локальный импорт

    return detect(REPO)


def pairs(home: Path) -> list[tuple[Path, Path]]:
    """Все файлы зеркала в паре (источник в форке, цель в профиле)."""
    out: list[tuple[Path, Path]] = []
    for src in sorted(MIRROR.rglob("*")):
        if not src.is_file() or any(part in SKIP for part in src.parts):
            continue
        rel = src.relative_to(MIRROR)
        if rel.parts[0] not in MIRROR_ROOTS:
            continue
        out.append((src, home / rel))
    return out


def same(a: Path, b: Path) -> bool:
    try:
        return b.is_file() and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def diff_note(a: Path, b: Path) -> str:
    """Короткая сводка различий: сколько строк +/- и в какую сторону объём."""
    try:
        old = b.read_text(encoding="utf-8", errors="replace").splitlines()
        new = a.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    added = removed = 0
    for line in difflib.unified_diff(old, new, n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return f"+{added}/-{removed} строк"


def run(direction: str, home: Path, check: bool) -> int:
    if not MIRROR.is_dir():
        print(f"нет зеркала: {MIRROR}")
        return 2
    if not home.is_dir():
        print(f"нет профиля Hermes: {home}")
        return 2

    src_root, dst_root = (MIRROR, home) if direction == "push" else (home, MIRROR)
    problems = 0
    changed: list[str] = []
    created: list[str] = []

    for mirror_file, home_file in pairs(home):
        src, dst = (mirror_file, home_file) if direction == "push" else (home_file, mirror_file)
        rel = mirror_file.relative_to(MIRROR).as_posix()
        if same(src, dst):
            continue
        problems += 1
        if not dst.is_file():
            print(f"  только в {src_root.name}: {rel}")
            created.append(rel)
        else:
            print(f"  различается: {rel}  ({diff_note(src, dst)})")
            changed.append(rel)
        if not check:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    if not problems:
        print(f"синхронизировано: {len(pairs(home))} файл(ов), дрейфа нет ({direction})")
        return 0

    if check:
        print(f"дрейф: {problems} файл(ов) — запусти без --check, чтобы развернуть")
        return 1

    verb = "развёрнуто в профиль" if direction == "push" else "забрано в форк"
    print(f"{verb}: обновлено {len(changed) + len(created)} из {len(pairs(home))}")

    if direction == "push":
        restart = [rel for rel in changed + created if Path(rel).name in NEEDS_RESTART]
        if restart:
            print("нужен рестарт dashboard (роутеры монтируются на старте): "
                  + ", ".join(restart))
            print("  python tools/restart_dashboard.bat  (или двойной клик)")
        if any(rel.endswith("plugin.js") for rel in changed + created):
            print("plugin.js подхватится хот-релоадом панели — переключать ничего не нужно")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Зеркало hermes/ (форк) ↔ $HERMES_HOME")
    ap.add_argument("--check", action="store_true", help="только показать дрейф, ничего не писать")
    ap.add_argument("--pull", action="store_true", help="профиль → форк (забрать правку в git)")
    ap.add_argument("--hermes-home", default="",
                    help="путь профиля Hermes (по умолчанию: HERMES_HOME или типовые места)")
    args = ap.parse_args(argv)

    home = hermes_home(args.hermes_home)
    direction = "pull" if args.pull else "push"
    print(f"{MIRROR}  ->  {home / 'desktop-plugins'}   [{direction}"
          f"{', check' if args.check else ''}]")
    return run(direction, home, args.check)


if __name__ == "__main__":
    sys.exit(main())
