#!/usr/bin/env python3
"""Установка панели BOOK → SKILL в профиль Hermes — в том числе на другой машине.

Что делает (детерминированно, без LLM)
--------------------------------------
1. Находит профиль Hermes: ``--hermes-home`` → ``HERMES_HOME`` → типовые места
   (портативная раскладка ``<Hermes>/data/hermes``, ``~/.hermes`` и т. п.).
2. Раскладывает зеркало ``hermes/`` ЭТОГО клона в профиль:

       desktop-plugins/b2s/plugin.js                  панель в правом rail'е
       plugins/b2s/plugin.yaml                        классификация плагина
       plugins/b2s/__init__.py                        register() — без него backend не грузится
       plugins/b2s/dashboard/plugin_api.py            REST-маршруты /api/plugins/b2s/*
       plugins/b2s/dashboard/manifest.json            api: true

3. Пишет локальный ``dashboard/config.json``: путь клона и интерпретатор для
   гейтов (``config.json`` — НЕ в git, он у каждой машины свой).
4. Проверяет окружение: зависимости интерпретатора (``trafilatura``,
   ``beautifulsoup4``), внешние инструменты (``pdftotext``), запись ``b2s`` в
   ``plugins.enabled`` профиля — и печатает точные команды для добора.
5. Говорит, как перезапустить dashboard, чтобы маршруты смонтировались.

Использование
-------------
    python tools/install_plugin.py                  # поставить / обновить
    python tools/install_plugin.py --check          # только план, ничего не писать
    python tools/install_plugin.py --hermes-home D:/Hermes/data/hermes
    python tools/install_plugin.py --enable         # просто вызвать `hermes plugins enable b2s`

Чем отличается от ``tools/sync_hermes.py``
------------------------------------------
``sync`` — про разработку на этой машине: двусторонний (ещё и забирает правки
из профиля в git). ``install`` — про развёртывание на чужой: односторонний,
создаёт ``config.json`` и проверяет, что окружение вообще способно работать.
"""
from __future__ import annotations

import argparse
import filecmp
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from hermes_paths import (  # noqa: E402  — путь к профилю ищем общим кодом
    ENV_FORK,
    ENV_PYTHON,
    ENV_HOME,
    home_candidates,
    detect_hermes_home,
    guess_python,
    plugin_dir,
    pane_dir,
)

MIRROR = REPO / "hermes"
ROOTS = ("desktop-plugins", "plugins")
SKIP = {"__pycache__", ".DS_Store", "config.json", "config.example.json"}
PY_PACKAGES = ("trafilatura", "bs4")
PLUGIN_NAME = "b2s"


# ── план копирования ─────────────────────────────────────────────────────────
def pairs(home: Path) -> list[tuple[Path, Path]]:
    """Пары (файл зеркала → файл профиля) для развёртывания."""
    out: list[tuple[Path, Path]] = []
    for src in sorted(MIRROR.rglob("*")):
        if not src.is_file() or any(part in SKIP for part in src.parts):
            continue
        rel = src.relative_to(MIRROR)
        if rel.parts[0] not in ROOTS:
            continue
        out.append((src, home / rel))
    return out


def state(src: Path, dst: Path) -> str:
    """``new`` / ``changed`` / ``same`` — что установка сделает с файлом."""
    if not dst.is_file():
        return "new"
    try:
        return "same" if filecmp.cmp(src, dst, shallow=False) else "changed"
    except OSError:
        return "changed"


def config_payload(home: Path) -> dict:
    """Локальный config.json плагина: где клон и чем гонять гейты."""
    return {
        "_comment": "Создано tools/install_plugin.py. Локально для этой машины, в git не хранится.",
        "fork": str(REPO.resolve()),
        "python": guess_python(home),
        "installed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "plugin_version": _plugin_version(),
    }


def _plugin_version() -> str:
    """Версия из plugin.yaml — чтобы в профиле было видно, что стоит."""
    yaml_file = MIRROR / "plugins" / PLUGIN_NAME / "plugin.yaml"
    try:
        for line in yaml_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "?"


# ── проверки окружения ───────────────────────────────────────────────────────
def missing_python_packages(python: str) -> list[str]:
    """Каких пакетов не хватает интерпретатору, которым пойдут гейты."""
    code = ("import importlib.util as u;"
            f"print(','.join(p for p in {PY_PACKAGES!r} if not u.find_spec(p)))")
    try:
        proc = subprocess.run([python, "-c", code], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"не удалось запустить интерпретатор ({type(exc).__name__})"]
    if proc.returncode != 0:
        return [(proc.stderr or proc.stdout or "").strip()[-300:]]
    return [name for name in (proc.stdout or "").strip().split(",") if name]


def plugin_enabled(home: Path) -> bool | None:
    """Есть ли ``b2s`` в ``plugins.enabled`` профиля (None — конфиг не найден)."""
    cfg = home / "config.yaml"
    try:
        lines = cfg.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    in_plugins = in_enabled = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_plugins = line.split(":", 1)[0].strip() == "plugins"
            in_enabled = False
            continue
        if not in_plugins:
            continue
        key = line.strip().split(":", 1)[0]
        if key in ("enabled", "disabled"):
            in_enabled = key == "enabled"
            # инлайновая форма: enabled: [b2s, ...]
            if in_enabled and line.split(":", 1)[1].strip().startswith("["):
                return PLUGIN_NAME in line.split(":", 1)[1]
            continue
        if in_enabled and line.lstrip().startswith("-"):
            if line.lstrip().lstrip("-").strip().strip("\"'") == PLUGIN_NAME:
                return True
    return False if in_plugins or any("plugins:" in l for l in lines) else None


def restart_hint(home: Path) -> str:
    """Команда рестарта dashboard: своя у клона, иначе — общий совет."""
    local = REPO / "tools" / "restart_dashboard.bat"
    if local.is_file():
        return f'"{local}"  (или: python "{local}")'
    return ("перезапусти Hermes desktop / службу dashboard — роутеры плагина "
            "монтируются только на старте процесса")


# ── установка ────────────────────────────────────────────────────────────────
def run(home: Path, check: bool, enable: bool) -> int:
    print(f"клон:   {REPO}")
    print(f"профиль: {home}")
    if not home.is_dir():
        print("профиля нет — сначала поставь Hermes или укажи --hermes-home")
        return 2

    planned = pairs(home)
    if not planned:
        print(f"в зеркале нет файлов ({MIRROR}) — нечего ставить")
        return 2

    restarts: list[str] = []
    copied = 0
    for src, dst in planned:
        rel = dst.relative_to(home).as_posix()
        st = state(src, dst)
        mark = {"new": "＋", "changed": "±", "same": "="}[st]
        print(f"  {mark} {rel}")
        if st != "same":
            restarts.append(dst.name)
        if check or st == "same":
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1

    cfg_file = plugin_dir(home) / "dashboard" / "config.json"
    payload = config_payload(home)
    old_cfg: dict = {}
    try:
        loaded = json.loads(cfg_file.read_text(encoding="utf-8"))
        old_cfg = loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        old_cfg = {}
    fork_changed = old_cfg.get("fork") not in (None, payload["fork"])
    cfg_state = "new" if not cfg_file.is_file() else ("changed" if fork_changed else "same")
    print(f"  {'.' if cfg_state == 'same' else '+'} plugins/{PLUGIN_NAME}/dashboard/config.json"
          f"  (config: fork={payload['fork']})")
    if cfg_state != "same":
        restarts.append("config.json")
    if not check and cfg_state != "same":
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        copied += 1
    if old_cfg and fork_changed:
        print(f"    было: fork={old_cfg.get('fork')}")

    if check:
        print(f"\n--check: изменений {len([1 for s, d in planned if state(s, d) != 'same']) + (cfg_state != 'same')}"
              f", записей {copied} (ничего не тронуто)")
        return 0

    # ── окружение ────────────────────────────────────────────────────────────
    print("\nокружение:")
    print(f"  интерпретатор гейтов: {payload['python']}")
    missing = missing_python_packages(payload["python"])
    if missing:
        print(f"  ✗ нет пакетов: {', '.join(missing)}")
        print(f"    доустанови: uv pip install --python \"{payload['python']}\" trafilatura beautifulsoup4")
    else:
        print("  ✓ trafilatura, beautifulsoup4")
    if shutil.which("pdftotext"):
        print("  ✓ pdftotext (PDF-режим text)")
    else:
        print("  · pdftotext нет — PDF-книги не разберутся; поставь poppler и добавь в PATH")

    enabled = plugin_enabled(home)
    if enabled:
        print(f"  ✓ плагин {PLUGIN_NAME} в plugins.enabled")
    elif enabled is None:
        print(f"  · config.yaml профиля не найден — включи плагин сам: hermes plugins enable {PLUGIN_NAME}")
    else:
        print(f"  ✗ плагин {PLUGIN_NAME} не в plugins.enabled — backend не смонтируется")
        if enable:
            print(f"    включаю: hermes plugins enable {PLUGIN_NAME}")
            try:
                proc = subprocess.run(["hermes", "plugins", "enable", PLUGIN_NAME],
                                      capture_output=True, text=True, encoding="utf-8",
                                      errors="replace", timeout=120)
                print(f"    hermes вернул код {proc.returncode}: "
                      f"{(proc.stdout or proc.stderr or '').strip()[-300:]}")
            except (OSError, subprocess.TimeoutExpired) as exc:
                print(f"    не вышло ({type(exc).__name__}) — включи вручную: hermes plugins enable {PLUGIN_NAME}")
        else:
            print(f"    включи: hermes plugins enable {PLUGIN_NAME}   (или повтори с --enable)")

    # ── что дальше ───────────────────────────────────────────────────────────
    print(f"\nзаписано файлов: {copied}")
    if set(restarts) & {"plugin_api.py", "manifest.json", "config.json"}:
        print("нужен рестарт dashboard (роутеры монтируются на старте):")
        print(f"  {restart_hint(home)}")
    if "plugin.js" in restarts:
        print("plugin.js панель подхватывает хот-релоадом — переоткрывать чат не нужно")
    print("проверка: python tools/probe_route.py skills   (ждём 200)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Установка панели BOOK → SKILL в профиль Hermes")
    ap.add_argument("--check", action="store_true", help="показать план и выйти, ничего не писать")
    ap.add_argument("--enable", action="store_true",
                    help=f"сам вызвать `hermes plugins enable {PLUGIN_NAME}`, если плагин не включён")
    ap.add_argument("--hermes-home", default="",
                    help=f"путь профиля Hermes (иначе ${ENV_HOME} или типовые места)")
    args = ap.parse_args(argv)

    home = Path(args.hermes_home) if args.hermes_home else detect_hermes_home(REPO)
    if home is None:
        print("не нашёл профиль Hermes — укажи его явно:")
        print("  python tools/install_plugin.py --hermes-home <каталог с config.yaml и skills/>")
        print("  (или задай переменную окружения " + ENV_HOME + ")")
        print("типовые места, которые проверял:")
        for cand in home_candidates(REPO):
            print(f"  · {cand}")
        print(f"подсказка: путь к форку можно задать и через {ENV_FORK}, "
              f"интерпретатор — через {ENV_PYTHON}")
        return 2
    return run(home, args.check, args.enable)


if __name__ == "__main__":
    sys.exit(main())
