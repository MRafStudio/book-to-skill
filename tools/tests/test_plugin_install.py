#!/usr/bin/env python3
"""Переносимость панели BOOK → SKILL: установка на «чужую» машину.

Песочница: ``HERMES_HOME=D:/tmp/b2s-install-home`` — фейковый профиль с
``config.yaml`` и ``skills/``. Клон настоящий, профиль пользователя не трогаем
(установщик зовём с ``--hermes-home``).

Что проверяем
-------------
1. ``--check`` показывает план и НИЧЕГО не пишет;
2. установка раскладывает ВСЕ файлы плагина, включая ``plugin.yaml`` и
   ``__init__.py`` — без них backend не грузится и панель молчит;
3. ``config.json`` создан установщиком и указывает настоящий путь клона;
4. машинных путей в коде нет: на чистой машине (без ``config.json`` и без
   ``B2S_FORK``) плагин отвечает 503 с инструкцией, а не падает и не лезет на
   путь чужой машины;
5. детект профиля работает по ``HERMES_HOME`` и не подсовывает чужой каталог.
"""
from __future__ import annotations

import filecmp
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "tools"
MIRROR = REPO / "hermes"
FAKE = Path("D:/tmp/b2s-install-home")
PY = sys.executable

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    """``detail`` печатается только при провале, ``note`` — при успехе."""
    checks.append((name, ok))
    if ok:
        print(f"  OK   {name}" + (f" — {note}" if note else ""))
    else:
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


def run(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([PY, str(TOOLS / "install_plugin.py"), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180, env=env)


def load_plugin_api(path: Path, env_extra: dict):
    """Загрузить plugin_api.py как модуль (fastapi/pydantic есть в venv Hermes).

    ``dont_write_bytecode`` — чтобы проверка не насорила ``__pycache__`` в зеркале.
    """
    env = dict(os.environ)
    env.update(env_extra)
    old = dict(os.environ)
    sys.dont_write_bytecode = True
    os.environ.clear()
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location("b2s_probe_plugin_api", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["b2s_probe_plugin_api"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        os.environ.clear()
        os.environ.update(old)


print("=== 0. песочница: фейковый профиль ===")
shutil.rmtree(FAKE, ignore_errors=True)
(FAKE / "skills").mkdir(parents=True, exist_ok=True)
(FAKE / "config.yaml").write_text("plugins:\n  enabled:\n    - b2s\n", encoding="utf-8")

print("\n=== 1. --check: показывает план, ничего не пишет ===")
res = run("--hermes-home", str(FAKE), "--check")
print("\n".join("    " + line for line in res.stdout.splitlines()))
check("--check вернул 0", res.returncode == 0, f"rc={res.returncode} {res.stderr.strip()[:200]}")
check("ничего не записано", not (FAKE / "plugins").exists())
check("без машинных путей в выводе?", "D:/NEURO" not in res.stdout,
      "в плане всплыл путь профиля этой машины")

print("\n=== 2. установка ===")
res = run("--hermes-home", str(FAKE))
print("\n".join("    " + line for line in res.stdout.splitlines()))
check("install вернул 0", res.returncode == 0, f"rc={res.returncode} {res.stderr.strip()[:300]}")

expected = [
    "desktop-plugins/b2s/plugin.js",
    "plugins/b2s/plugin.yaml",
    "plugins/b2s/__init__.py",
    "plugins/b2s/dashboard/plugin_api.py",
    "plugins/b2s/dashboard/manifest.json",
    "plugins/b2s/dashboard/config.json",
]
for rel in expected:
    target = FAKE / rel
    src = MIRROR / rel.replace("plugins/b2s/dashboard/config.json",
                               "plugins/b2s/dashboard/config.example.json")
    if rel.endswith("config.json"):
        check(f"создан {rel}", target.is_file())
    else:
        ok = target.is_file() and filecmp.cmp(src, target, shallow=False)
        check(f"на месте и совпадает с зеркалом: {rel}", ok)

print("\n=== 3. config.json: путь клона и интерпретатор ===")
cfg = json.loads((FAKE / "plugins/b2s/dashboard/config.json").read_text(encoding="utf-8"))
check("fork = настоящий клон", Path(cfg["fork"]).resolve() == REPO, cfg["fork"])
check("python существует", Path(cfg["python"]).is_file(), cfg["python"])
check("config.json не в зеркале (не уезжает на другие машины)",
      not (MIRROR / "plugins/b2s/dashboard/config.json").exists())
check("config.json закрыт .gitignore",
      "hermes/plugins/b2s/dashboard/config.json" in (REPO / ".gitignore").read_text(encoding="utf-8"))

print("\n=== 4. чистая машина: нет config.json и нет B2S_FORK ===")
probe = load_plugin_api(MIRROR / "plugins/b2s/dashboard/plugin_api.py",
                        {"B2S_FORK": "", "B2S_PYTHON": "", "HERMES_HOME": str(FAKE)})
cfg_clean = probe.config()
check("fork не подставлен из воздуха", cfg_clean["fork"] == "", repr(cfg_clean["fork"]))
check("никакого пути этой машины в дефолтах",
      "BOOK-TO-SKILL" not in cfg_clean["fork"] and "NEURO" not in cfg_clean["fork"])
try:
    probe.core()
    check("core() без конфига падает понятной ошибкой", False, "503 не поднялся")
except Exception as exc:  # fastapi.HTTPException — статус 503 с инструкцией
    text = str(getattr(exc, "detail", exc))
    status = getattr(exc, "status_code", None)
    check("core() без конфига → 503 с инструкцией",
          status == 503 and "install_plugin" in text, f"status={status} {text[:120]}")

print("\n=== 5. детект профиля ===")
sys.path.insert(0, str(TOOLS))
import hermes_paths  # noqa: E402

os.environ["HERMES_HOME"] = str(FAKE)
check("явный HERMES_HOME уважается как есть",
      hermes_paths.detect_hermes_home(REPO) == FAKE.resolve())
check("профиль узнаётся по config.yaml/skills",
      hermes_paths.hermes_home(REPO) == FAKE.resolve())
os.environ.pop("HERMES_HOME", None)
found = hermes_paths.detect_hermes_home(Path("D:/tmp/b2s-no-such-clone/deep"))
check("без HERMES_HOME чужой каталог не подставляется", found != FAKE.resolve(),
      f"вернул {found}")
os.environ["HERMES_HOME"] = str(FAKE)

print("\n=== 6. запись в plugins.enabled ===")
check("b2s найден в enabled", hermes_paths.detect_hermes_home(REPO) is not None)
spec = importlib.util.spec_from_file_location("b2s_install_probe", TOOLS / "install_plugin.py")
install_mod = importlib.util.module_from_spec(spec)
sys.modules["b2s_install_probe"] = install_mod
spec.loader.exec_module(install_mod)
check("plugin_enabled → True для [b2s]", install_mod.plugin_enabled(FAKE) is True)
(FAKE / "config.yaml").write_text("plugins:\n  enabled:\n    - memtensor\n", encoding="utf-8")
check("plugin_enabled → False без b2s", install_mod.plugin_enabled(FAKE) is False)

shutil.rmtree(FAKE, ignore_errors=True)

failed = [name for name, ok in checks if not ok]
print(f"\n=== итог: {len(checks) - len(failed)}/{len(checks)} проверок пройдено ===")
for name in failed:
    print(f"  FAIL: {name}")
sys.exit(1 if failed else 0)
