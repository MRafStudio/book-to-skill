#!/usr/bin/env python3
"""Проверка REST-маршрута панели через auth-гейт dashboard'а — без догадок по 401.

Зачем
-----
`/api/plugins/*` в dashboard закрыт сессией, и гейт стоит **до** роутинга: и
незнакомый путь, и клиент без cookie получают один и тот же 401. То есть по
коду ответа нельзя понять, смонтирован маршрут или его нет. Честный тест —
получить сессию и сравнить два ответа: нужный путь и **заведомо несуществующий
контроль** (`…/nope`). 200 + 404 = маршрут есть; 404 + 404 = маршрута нет.

Использование
-------------
    python tools/probe_route.py categories
    python tools/probe_route.py text --post '{"limit":0}'
    python tools/probe_route.py categories --port 9119
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

HERMES_HOME = Path("D:/NEURO/Hermes/data/hermes")
PLUGIN_ID = "b2s"


def dashboard_creds() -> tuple[str, str]:
    """Логин/пароль dashboard из config.yaml (dashboard.username/password)."""
    cfg = HERMES_HOME / "config.yaml"
    user = password = ""
    for line in cfg.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("username:"):
            user = stripped.split(":", 1)[1].strip().strip("'\"")
        elif stripped.startswith("password:"):
            password = stripped.split(":", 1)[1].strip().strip("'\"")
    if not user or not password:
        raise SystemExit("не нашёл dashboard.username/password в config.yaml")
    return user, password


def session(port: int) -> urllib.request.OpenerDirector:
    """Сессия с cookie: provider обязателен, иначе 422."""
    user, password = dashboard_creds()
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body = json.dumps({"provider": "basic", "username": user, "password": password}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/auth/password-login", data=body,
        headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8") or "{}")
    if not payload.get("ok"):
        raise SystemExit(f"логин не прошёл: {payload}")
    return opener


def probe(opener, port: int, path: str, method: str = "GET", post: str = "") -> tuple[int, str]:
    url = f"http://127.0.0.1:{port}/api/plugins/{PLUGIN_ID}/{path}"
    data = post.encode() if post else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", "replace")
            return resp.status, text
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="probe a b2s REST route honestly")
    parser.add_argument("path", help="путь без префикса, напр. categories")
    parser.add_argument("--port", type=int, default=9119)
    parser.add_argument("--post", default="", help="JSON-тело; включает POST")
    args = parser.parse_args(argv)

    opener = session(args.port)
    code, body = probe(opener, args.port, args.path,
                       "POST" if args.post else "GET", args.post)
    control, _ = probe(opener, args.port, "nope-" + args.path)
    print(f"/{args.path}: HTTP {code}")
    print(body[:600])
    print(f"контроль (несуществующий): HTTP {control}")
    if code == 200 and control == 404:
        print("ВЕРДИКТ: маршрут смонтирован ✅")
        return 0
    if code == 404 and control == 404:
        print("ВЕРДИКТ: маршрута в живом процессе НЕТ — нужен перезапуск dashboard ⏳")
        return 2
    print("ВЕРДИКТ: непонятно (см. коды выше)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
