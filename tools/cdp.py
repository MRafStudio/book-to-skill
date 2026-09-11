#!/usr/bin/env python3
"""Живой Chrome по CDP: замер/дамп локальной страницы БЕЗ nag про профиль.

Запускает СВОЙ headless Chrome с отдельным --user-data-dir (поэтому не трогает
живой профиль пользователя и не требует «Allow remote debugging»), открывает
страницу и выполняет в ней JS. То, что нужно для проверки вёрстки виджета.

Использование:
    python tools/cdp.py <file-or-url> "<js-выражение>" [--width 520] [--height 900] [--shot out.png]

Пример:
    python tools/cdp.py dashboard/render.html \
      "(function(){return document.documentElement.scrollWidth})()" --width 460

Порядок: если порт 9223 уже отвечает — переиспользуем запущенный инстанс
(так повторные вызовы летают за доли секунды).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
PORT = int(os.environ.get("B2S_CDP_PORT", "9223"))
PROFILE = os.environ.get("B2S_CDP_PROFILE", "D:/tmp/b2s-chrome-profile")


def find_chrome() -> str:
    for c in CHROME_CANDIDATES:
        if pathlib.Path(c).exists():
            return c
    raise SystemExit("Chrome не найден; укажи путь в CHROME_CANDIDATES")


def http_json(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


def port_alive() -> bool:
    try:
        http_json("/json/version")
        return True
    except Exception:
        return False


def launch(width: int, height: int) -> subprocess.Popen | None:
    if port_alive():
        return None
    exe = find_chrome()
    proc = subprocess.Popen(
        [
            exe,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--remote-debugging-port={PORT}",
            f"--user-data-dir={PROFILE}",
            f"--window-size={width},{height}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        if port_alive():
            return proc
        time.sleep(0.25)
    raise SystemExit(f"Chrome не открыл CDP-порт {PORT}")


async def ws_call(ws, msg_id: int, method: str, params: dict | None = None, timeout: float = 20):
    await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        data = json.loads(raw)
        if data.get("id") == msg_id:
            if "error" in data:
                raise SystemExit(f"CDP error on {method}: {data['error']}")
            return data.get("result", {})
        # события (Page.loadEventFired и т.п.) просто пропускаем


async def run(url: str, expr: str, width: int, height: int, shot: str | None, wait_ms: int):
    import websockets

    targets = http_json("/json/list")
    page = next((t for t in targets if t.get("type") == "page"), None)
    if page is None:
        raise SystemExit("нет page-таргета в CDP")

    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024) as ws:
        i = 1
        await ws_call(ws, i, "Page.enable"); i += 1
        await ws_call(ws, i, "Emulation.setDeviceMetricsOverride",
                      {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False}); i += 1
        await ws_call(ws, i, "Page.navigate", {"url": url}); i += 1
        await asyncio.sleep(max(wait_ms, 0) / 1000.0)
        res = await ws_call(ws, i, "Runtime.evaluate",
                            {"expression": expr, "returnByValue": True, "awaitPromise": True}); i += 1
        value = res.get("result", {}).get("value")
        if value is None and res.get("exceptionDetails"):
            print("JS ИСКЛЮЧЕНИЕ:", json.dumps(res["exceptionDetails"], ensure_ascii=False)[:800])
        else:
            print(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=1))
        if shot:
            data = await ws_call(ws, i, "Page.captureScreenshot",
                                 {"format": "png", "captureBeyondViewport": True}); i += 1
            pathlib.Path(shot).write_bytes(__import__("base64").b64decode(data["data"]))
            print(f"[screenshot] {shot}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("expr")
    ap.add_argument("--width", type=int, default=520)
    ap.add_argument("--height", type=int, default=900)
    ap.add_argument("--shot", default=None)
    ap.add_argument("--wait", type=int, default=1200, help="мс после навигации")
    a = ap.parse_args()

    target = a.target
    if not target.startswith(("http://", "https://", "file://", "about:")):
        target = pathlib.Path(target).resolve().as_uri()

    proc = launch(a.width, a.height)
    try:
        asyncio.run(run(target, a.expr, a.width, a.height, a.shot, a.wait))
    finally:
        if proc is not None:
            proc.terminate()


if __name__ == "__main__":
    sys.exit(main())
