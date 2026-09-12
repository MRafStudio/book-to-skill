#!/usr/bin/env python3
"""Общие пути BOOK → SKILL: профиль Hermes и интерпретатор для гейтов.

Зачем отдельный модуль
----------------------
Одно и то же ищут три входа: ядро (``tools/api.py``), установщик плагина
(``tools/install_plugin.py``) и синхронизатор зеркала (``tools/sync_hermes.py``).
Держать путь конкретной машины в коде нельзя: панель ставится и на другие
компьютеры, где и профиль, и клон лежат в других местах.

Профиль Hermes узнаётся по содержимому, а не по имени каталога: у него есть
``skills/`` либо ``config.yaml``. Этого достаточно, чтобы не перепутать профиль
с произвольной папкой рядом.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENV_HOME = "HERMES_HOME"
ENV_PYTHON = "B2S_PYTHON"
ENV_FORK = "B2S_FORK"


def _is_profile(path: Path) -> bool:
    """Похоже ли это на профиль Hermes: скиллы или конфиг на месте."""
    return (path / "skills").is_dir() or (path / "config.yaml").is_file()


def home_candidates(repo: Path | None = None) -> list[Path]:
    """Типовые места профиля — в порядке проверки (env идёт первым отдельно)."""
    root = (repo or REPO).resolve()
    out: list[Path] = []
    for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"),
                 os.environ.get("XDG_CONFIG_HOME")):
        if base:
            out += [Path(base) / "Hermes", Path(base) / "hermes"]
    out += [
        Path.home() / ".hermes",
        Path.home() / ".config" / "hermes",
        # портативная раскладка: профиль рядом с клоном (<Hermes>/data/hermes)
        root.parent / "data" / "hermes",
        root.parent.parent / "data" / "hermes",
    ]
    return out


def detect_hermes_home(repo: Path | None = None) -> Path | None:
    """Найти профиль Hermes или вернуть None (без исключений — для сообщений).

    Явно заданный ``HERMES_HOME`` уважается **как есть**, без проверки на
    существование: это прямое указание пользователя (в том числе на путь
    песочницы, который ещё не создан). Детект по типовым местам — только когда
    переменная не задана.
    """
    env = os.environ.get(ENV_HOME)
    if env:
        return Path(env).resolve()
    for cand in home_candidates(repo):
        try:
            if _is_profile(cand):
                return cand.resolve()
        except OSError:
            continue
    return None


def hermes_home(repo: Path | None = None) -> Path:
    """Профиль Hermes либо понятная ошибка «что задать»."""
    home = detect_hermes_home(repo)
    if home is None:
        raise ValueError(
            "не найден профиль Hermes: задай переменную окружения HERMES_HOME "
            "(каталог с config.yaml и skills/) или положи config.json плагина "
            "рядом с plugin_api.py — его пишет tools/install_plugin.py"
        )
    return home


def guess_python(home: Path | None = None) -> str:
    """Интерпретатор с зависимостями форка (trafilatura/bs4).

    В службе dashboard ``sys.executable`` — это hermes.exe, а не python, поэтому
    сначала берём venv профиля и только потом откатываемся на текущий процесс.
    """
    candidates: list[Path] = []
    if home is not None:
        candidates += [
            home / "hermes-agent" / "venv" / "Scripts" / "python.exe",  # Windows
            home / "hermes-agent" / "venv" / "bin" / "python",          # POSIX
        ]
    candidates.append(Path(sys.executable))
    for cand in candidates:
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            continue
    return sys.executable


def plugin_dir(home: Path) -> Path:
    """Каталог REST-половины плагина в профиле."""
    return home / "plugins" / "b2s"


def pane_dir(home: Path) -> Path:
    """Каталог панельной половины плагина в профиле."""
    return home / "desktop-plugins" / "b2s"
