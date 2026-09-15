#!/usr/bin/env python3
"""Полный прогон тестов проекта: все ``tools/tests/test_*.py`` по очереди.

Зачем отдельный файл: прогон нужен после каждой правки плагина, а heredoc в шелле
на длинных командах рвётся (проверено на себе). Здесь одно место и один итог.

Запуск (интерпретатор - venv профиля, иначе падают тесты, которым нужны trafilatura
и fastapi):

    D:/NEURO/Hermes/data/hermes/hermes-agent/venv/Scripts/python.exe tools/tests/run_all.py

Код выхода: 0 - все зелёные, 1 - есть провалы (их имена печатаются).
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]


def main() -> int:
    py = sys.executable
    files = sorted((REPO / "tools" / "tests").glob("test_*.py"))
    bad: list[str] = []
    for f in files:
        r = subprocess.run([py, str(f)], capture_output=True, text=True, timeout=900)
        tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
        print(f"{f.name:34s} {'OK  ' if r.returncode == 0 else 'FAIL'} {tail[0][:80]}", flush=True)
        if r.returncode != 0:
            bad.append(f.name)
            print((r.stdout or "")[-400:], flush=True)
    print(f"\nфайлов: {len(files)} | провалов: {len(bad)}")
    if bad:
        print("провалились: " + ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())