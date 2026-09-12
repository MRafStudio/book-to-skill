#!/usr/bin/env python3
"""Перезапуск ТОЛЬКО dashboard-службы Hermes (роутеры плагина монтируются на старте).

Зачем
-----
Новый REST-маршрут плагина (`plugins/<id>/dashboard/plugin_api.py`) виден живому
процессу только после перезапуска: маршруты монтируются при старте. Чат идёт
через отдельный процесс (`hermes_cli.main serve`) — его не трогаем.

Почему не в .bat и не на `sc`
-----------------------------
Имя службы зависит от пути установки Hermes («HermesGateway (D:_NEURO_Hermes)»),
то есть путь конкретной машины попал бы в версионируемый файл, и на другом
компьютере рестарт молча не нашёл бы службу. Поэтому имя не пишем, а ищем по
префиксу `HermesGateway`. Парсить вывод `sc` маркерами тоже нельзя: на русской
Windows он переводит их («ИМЯ_СЛУЖБЫ», «СОСТОЯНИЕ» — проверено), поэтому основа
— PowerShell (его значения `Running`/`Stopped` от языка не зависят), а `sc` —
фоллбэк по латинским токенам состояния. Если прав не хватает, скрипт сам
перезапускается от админа (UAC).

Использование
-------------
    python tools/restart_dashboard.py            # найти службу и перезапустить
    python tools/restart_dashboard.py --list     # только показать, что найдено
    python tools/restart_dashboard.py --name "HermesGateway (D:_NEURO_Hermes)"
"""
from __future__ import annotations

import argparse
import ctypes
import subprocess
import sys
import time
from pathlib import Path

SERVICE_PREFIX = "HermesGateway"
STATE_WAIT = 30.0   # сколько секунд ждём смены состояния службы
_PS = ["powershell", "-NoProfile", "-Command"]
_PS_STATUS = {"running": "RUNNING", "stopped": "STOPPED", "startpending": "START_PENDING",
              "stoppending": "STOP_PENDING", "paused": "PAUSED", "pausepending": "PAUSE_PENDING"}


def _run(cmd: list[str], timeout: int = 40) -> tuple[int, str]:
    """Запустить команду и вернуть (код, вывод). Падение команды — не исключение."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()


def _ps(script: str, timeout: int = 40) -> tuple[int, str]:
    """PowerShell-однострочник: значения служб у него английские при любой локали."""
    return _run([*_PS, script], timeout)


def _ps_quote(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def find_services() -> list[str]:
    """Службы dashboard по префиксу имени — одинаково на этой и на чужой машине."""
    names: list[str] = []
    code, out = _ps(f"(Get-Service -Name '{SERVICE_PREFIX}*' | Select-Object "
                    f"-ExpandProperty Name)")
    if code == 0:
        names = [line.strip() for line in out.splitlines() if line.strip()]
    if names:
        return names
    code, out = _run(["sc", "query", "type=", "service", "state=", "all"])
    for line in out.splitlines():
        if ":" in line:
            tail = line.split(":", 1)[1].strip()
            if tail.startswith(SERVICE_PREFIX):
                names.append(tail)
    return names


def state(name: str) -> str:
    """Состояние службы: RUNNING / STOPPED / *_PENDING / UNKNOWN."""
    code, out = _ps(f"(Get-Service -Name {_ps_quote(name)}).Status")
    if code == 0 and out.strip():
        return _PS_STATUS.get(out.strip().lower(), out.strip().upper())
    code, out = _run(["sc", "query", name])
    upper = out.upper()   # сами состояния в sc всегда латиницей
    for want in ("STOP_PENDING", "START_PENDING", "RUNNING", "STOPPED", "PAUSED"):
        if want in upper:
            return want
    return "UNKNOWN"


def wait_state(name: str, want: str) -> bool:
    """Ждать смены состояния: остановка возвращает управление раньше реального стопа."""
    deadline = time.time() + STATE_WAIT
    while time.time() < deadline:
        if state(name) == want:
            return True
        time.sleep(1.0)
    return state(name) == want


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())   # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return False


def elevate(argv: list[str]) -> int:
    """Перезапуститься от админа (UAC) — иначе управление службой получит отказ."""
    script = str(Path(__file__).resolve())
    params = " ".join(f'"{a}"' for a in [script, *argv])
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(   # type: ignore[attr-defined]
            None, "runas", sys.executable, params, None, 1)
    except (AttributeError, OSError) as exc:
        print(f"не удалось запросить права администратора: {exc}")
        return 1
    if rc <= 32:
        print("запрос прав отклонён (UAC) — перезапусти панель вручную")
        return 1
    print("запрошен перезапуск от админа — подтверди окно UAC, дальше процесс продолжит сам")
    return 0


def cycle(name: str) -> int:
    """Стоп → ожидание → старт → ожидание, с откатом на `sc`, если PowerShell отказал."""
    code, out = _ps(f"Stop-Service -Name {_ps_quote(name)} -Force")
    if code != 0:
        code, out = _run(["sc", "stop", name])
    stopped = wait_state(name, "STOPPED")
    code, out = _ps(f"Start-Service -Name {_ps_quote(name)}")
    if code != 0:
        code, out = _run(["sc", "start", name])
    running = wait_state(name, "RUNNING")
    print(f"состояние после: {state(name)}")
    if stopped and running:
        print("готово: перезапущено — маршруты плагина перечитаны")
        return 0
    print("внимание: состояние не подтвердилось — проверь службу глазами (services.msc)")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="restart the Hermes dashboard service")
    parser.add_argument("--name", default="", help="точное имя службы (иначе ищем по префиксу)")
    parser.add_argument("--list", action="store_true", help="только показать найденные службы")
    parser.add_argument("--no-elevate", action="store_true", help="не просить права (для тестов)")
    args = parser.parse_args(argv)

    names = [args.name] if args.name else find_services()
    if not names:
        print(f"служба dashboard не найдена (искал по префиксу «{SERVICE_PREFIX}»).")
        print("проверь установку: hermes/INSTALL.md — панель работает и пока dashboard запущен вручную.")
        return 2
    if args.list:
        for name in names:
            print(f"{name}: {state(name)}")
        return 0

    name = names[0]
    print(f"служба: {name} · состояние до: {state(name)}")
    if not is_admin() and not args.no_elevate:
        return elevate(["--name", name])
    return cycle(name)


if __name__ == "__main__":
    sys.exit(main())
