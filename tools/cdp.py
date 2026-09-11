#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Обёртка: канонический Chrome-CDP инструмент живёт ВНЕ этого репозитория.

Сам инструмент — D:/NEURO/Hermes/scripts/py/chrome_cdp.py (репозиторий
Hermes-Portable-Scripts: живёт дольше любого проекта и переживает обновления
Hermes). Здесь только прокидка аргументов, чтобы из book-to-skill работал ровно
тот же код — и те же грабли, что описаны в скилле chrome-headless-cdp.

Пример:
    python tools/cdp.py dashboard/render.html --check-overflow --widths 300,460
    python tools/cdp.py https://example.com --sel "h2"

Если канонический файл переехал — задай путь в переменной CHROME_CDP_TOOL.
"""
from __future__ import annotations

import os
import runpy
import sys

CANDIDATES = [
    os.environ.get("CHROME_CDP_TOOL", ""),
    r"D:\NEURO\Hermes\scripts\py\chrome_cdp.py",
]


def main() -> int:
    for path in CANDIDATES:
        if path and os.path.isfile(path):
            sys.argv = [path, *sys.argv[1:]]
            runpy.run_path(path, run_name="__main__")
            return 0
    print(
        "Не найден chrome_cdp.py. Задай CHROME_CDP_TOOL=<путь> или верни "
        "канонический файл в D:\\NEURO\\Hermes\\scripts\\py\\.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
