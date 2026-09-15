#!/usr/bin/env python3
"""Локальный HTTP-сервер дашборда book-to-skill (план Б, форк MRafStudio).

Зачем он нужен
--------------
Правая панель Hermes и file-таб — поверхности ПРОСМОТРА: мост в чат
(``window.hermes``) инжектится только в инлайн-виджет ``::preview`` внутри чата,
поэтому кнопки в панели физически не могут ничего отправить агенту.

По HTTP этого ограничения нет: страница сама говорит с сервером через
``fetch``. Поэтому кнопки становятся настоящими — прогон каскада, подстановка
свежего состояния, заказ на генерацию черновика.

Что делает сервер
-----------------
    GET  /               страница дашборда (тот же ``dashboard/widget.html``)
    GET  /api/health     жив ли сервер и сколько уже работает
    GET  /api/state      текущее состояние (JSON)
    POST /api/state      запомнить поля страницы (src, strat, name, mode, ...)
    POST /api/rerun      прогнать каскад по state.src под state.strat
    POST /api/order      положить заказ на генерацию черновика в staging/.orders/

Слушает ТОЛЬКО 127.0.0.1. Зависимостей нет — чистый stdlib.

Запуск
------
    python tools/serve.py                 # http://127.0.0.1:8765/
    python tools/serve.py --port 8766
Открывать в Browser-табе панели Hermes или в обычном браузере.
"""
import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

# Отчёт печатает ✓ / ✗ и не-ASCII заголовки страниц.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import dashboard as dash  # noqa: E402  — сбор данных переиспользуем, не копируем
from book_to_skill.fetcher import Fetcher  # noqa: E402

STATE_FILE = REPO / "dashboard" / ".state.json"
ORDERS_DIR = REPO / "staging" / ".orders"
DEFAULT_SRC = "https://docs.python.org/3/library/pathlib.html"
LOG_FILE = REPO / "dashboard" / ".serve.log"

_lock = threading.Lock()
STARTED = time.time()


def say(text: str) -> None:
    """Печать в stdout + строка в dashboard/.serve.log.

    Лог — это ДОКАЗАТЕЛЬСТВО связи: в Browser-табе панели нет devtools, поэтому
    по строкам лога видно, что страница действительно открылась и её fetch
    дошёл до сервера (``GET /``, ``GET /api/health``, ``POST /api/rerun``).
    """
    line = f"{time.strftime('%H:%M:%S')} {text}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


# ── состояние ────────────────────────────────────────────────────────────────
def initial_state() -> dict:
    return {
        "src": DEFAULT_SRC,
        "strat": "",              # "" = авто-каскад
        "name": "",
        "mode": "technical",
        "depth": "reference",
        "lang": "ru",
        "cat": dash.DEFAULT_CATEGORY,
        "report": None,           # последний УДАЧНЫЙ отчёт
        "report_at": "",
        "last_error": None,       # последний провал: причина и время
        "history": [],
    }


def load_state() -> dict:
    st = initial_state()
    if STATE_FILE.is_file():
        try:
            saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                st.update({k: v for k, v in saved.items() if k in st})
        except (OSError, ValueError):
            pass
    return st


def save_state(st: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def failure_kind(src: str, strat: str, report: dict) -> str:
    """Где сорвался прогон: 'source' - источник не получен, 'strategy' - не подошёл разбор.

    Панель по этому признаку решает, ЧЕЙ шаг краснеет. Провал получения источника -
    это шаг 1 («Источник»): владелец ввёл в поле произвольную строку и получал красную
    границу у блока 2 («Анализ источника»), до которого дело даже не дошло. Красная
    граница блока 2 - только про сам разбор, когда источник уже есть на руках.
    """
    low = (src or "").lower()
    # Строка вообще не похожа на источник (владелец ввёл «1»): виноват шаг подачи,
    # чем бы ни была настроена очистка - до разбора дело не дошло.
    if not (low.startswith(("http://", "https://")) or Path((src or "").strip()).exists()):
        return "source"
    if strat == "raw-md" and not low.endswith((".md", ".markdown")):
        return "strategy"
    if low.endswith((".md", ".markdown")) and strat in ("trafilatura", "bs4", "stdlib"):
        return "strategy"
    return "source"


def explain_failure(src: str, strat: str, report: dict) -> str:
    """Человеческая причина провала вместо «no plugin matched this URL»."""
    low = (src or "").lower()
    if strat == "raw-md" and not low.endswith((".md", ".markdown")):
        return ("raw-md берёт только markdown-источники (.md или github blob→raw), "
                "а тут обычная страница. Выбери auto или trafilatura.")
    if low.endswith((".md", ".markdown")) and strat in ("trafilatura", "bs4", "stdlib"):
        return (f"{strat} разбирает HTML, а источник уже markdown. "
                "Для .md выбирай auto или raw-md.")
    notes = " ".join(report.get("notes") or []) or "ни одна стратегия не дала текста"
    return notes


def run_fetch(st: dict) -> dict:
    """Прогнать каскад по текущему источнику и стратегии; обновить state.

    Провал НЕ затирает последний удачный отчёт: иначе неудачный прогон (например,
    raw-md по HTML) выглядит как «всё сломалось», а прежний результат теряется.
    """
    src = (st.get("src") or "").strip()
    if not src:
        return {"ok": False, "error": "пустой источник"}
    strat = (st.get("strat") or "").strip()
    force = strat or None
    if force == "auto":
        force = None
    started = time.time()
    # The pane's engine switch (technical | text) belongs to the source, not to
    # the strategy: the local-file plugin hands it to the upstream extractor,
    # which picks docling vs pdftotext for PDFs.
    os.environ["B2S_EXTRACTION_MODE"] = (st.get("mode") or "technical").strip().lower()
    report = Fetcher(force=force).fetch_to_dir(src, str(REPO / "b2s_fetched"))
    report["_seconds"] = round(time.time() - started, 2)
    st["history"] = ([{
        "at": time.strftime("%H:%M:%S"),
        "src": src,
        "strat": strat or "auto",
        "won": report.get("strategy"),
        "chars": report.get("chars"),
        "junk": report.get("junk_total"),
        "seconds": report["_seconds"],
    }] + list(st.get("history") or []))[:12]
    if report.get("ok"):
        st["report"] = report
        st["report_at"] = time.strftime("%H:%M:%S")
        st["last_error"] = None
    else:
        st["last_error"] = {
            "at": time.strftime("%H:%M:%S"),
            "strat": strat or "auto",
            "src": src,
            "seconds": report["_seconds"],
            "message": explain_failure(src, strat, report),
            # Чей это провал: получение источника (шаг 1) или выбор разбора (шаг 2).
            "kind": failure_kind(src, strat, report),
        }
    save_state(st)
    return report


def state_report(st: dict) -> dict:
    """Отчёт из состояния; если его ещё нет — прогнать один раз."""
    if not st.get("report"):
        return run_fetch(st)
    return st["report"]


# ── данные для страницы (то же, что собирает tools/dashboard.py) ─────────────
def build_data() -> dict:
    with _lock:
        st = load_state()
        report = state_report(st)
        tries = Fetcher(force=(st.get("strat") or None))
        if st.get("strat") == "auto":
            tries = Fetcher(force=None)
        session = dash.session_info("")
        data = {
            "report": report,
            "plugins": [
                {"name": p.NAME, "priority": p.PRIORITY, "score": p.matches(report.get("url", ""))}
                for p in tries.plugins
            ],
            "categories": dash.discover_categories(),
            "engine": dash.engine_info(session),
            "default_category": dash.DEFAULT_CATEGORY,
            "suggested_name": dash.suggest_skill_name(
                report.get("url", ""), report.get("title", ""), report.get("source_file", "")
            ),
            "draft": dash.read_draft(dash.newest_draft_root()),
            "state": {k: v for k, v in st.items() if k not in ("report", "history")},
            "history": st.get("history") or [],
            "report_at": st.get("report_at") or "",
            "last_error": st.get("last_error"),
        }
        return data


def summary_line(report: dict) -> str:
    if not report.get("ok"):
        return "не удалось: ни одна стратегия не дала текста"
    return (f"стратегия {report.get('strategy')} · {report.get('chars')} симв · "
            f"~{report.get('est_tokens')} токенов · мусор {report.get('junk_total')} · "
            f"за {report.get('_seconds', '?')} с")


# ── HTTP ─────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "b2s-dashboard/0.1"

    # -- ответы
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, text: str) -> None:
        self._send(code, text.encode("utf-8"), "text/html; charset=utf-8")

    def _json(self, code: int, obj: dict) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # -- маршруты
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                data = build_data()
            except Exception as exc:  # noqa: BLE001 — страница обязана открыться
                self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                return
            template = (REPO / "dashboard" / "widget.html").read_text(encoding="utf-8")
            self._html(200, template.replace("__B2S_DATA__", json.dumps(data, ensure_ascii=False)))
        elif path == "/api/health":
            self._json(200, {"ok": True, "pid": os.getpid(), "alive_s": round(time.time() - STARTED, 1)})
        elif path == "/api/state":
            with _lock:
                st = load_state()
            self._json(200, {"ok": True, "state": {k: v for k, v in st.items()
                                                   if k not in ("report", "history")},
                             "history": st.get("history") or []})
        else:
            self._json(404, {"ok": False, "error": f"нет маршрута {path}"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._body()
        # Пустое тело — почти всегда потерянный payload (например, curl в MSYS его не донёс).
        # Молча подставлять дефолты нельзя: заказ уйдёт в очередь с пустым источником.
        # Строго только там, где поля реально нужны; для /api/state пустой объект безобиден.
        if not body and path in ("/api/rerun", "/api/order"):
            self._json(400, {"ok": False, "error": "пустое тело запроса: нужен JSON с полями"})
            return

        if path == "/api/state":
            with _lock:
                st = load_state()
                for key in ("src", "strat", "name", "mode", "depth", "lang", "cat"):
                    if key in body:
                        st[key] = body[key]
                save_state(st)
            self._json(200, {"ok": True})

        elif path == "/api/rerun":
            with _lock:
                st = load_state()
                for key in ("src", "strat", "name", "mode", "depth", "lang", "cat"):
                    if body.get(key):
                        st[key] = body[key]
                report = run_fetch(st)
                last_error = st.get("last_error")
                report_at = st.get("report_at") or ""
            # Провал возвращаем как ok+warning: страница должна перезагрузиться и показать
            # прежний удачный отчёт вместе с причиной, а не молча «ничего не изменилось».
            if report.get("ok"):
                self._json(200, {"ok": True, "message": summary_line(report),
                                 "report": report, "report_at": report_at})
            else:
                self._json(200, {"ok": True, "warning": True,
                                 "message": "прогон не дал текста: "
                                            + ((last_error or {}).get("message")
                                               or "источник не разобрался")
                                            + (f". Показан прежний удачный отчёт от {report_at}"
                                               if report_at else ""),
                                 "report": report, "report_at": report_at})

        elif path == "/api/order":
            order = {
                "kind": body.get("kind") or "draft",
                "src": body.get("src") or "",
                "name": body.get("name") or "",
                "strat": body.get("strat") or "auto",
                "mode": body.get("mode") or "",
                "depth": body.get("depth") or "",
                "lang": body.get("lang") or "",
                "cat": body.get("cat") or "",
                "notes": body.get("notes") or "",
                "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            ORDERS_DIR.mkdir(parents=True, exist_ok=True)
            (ORDERS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{order['kind']}.json").write_text(
                json.dumps(order, ensure_ascii=False, indent=1), encoding="utf-8")
            self._json(200, {"ok": True, "message": f"заказ «{order['kind']}» в очереди - агент подхватит",
                             "order": order})

        else:
            self._json(404, {"ok": False, "error": f"нет маршрута {path}"})

    # -- лог: по этим строкам видно, что страница в панели реально ходит
    def log_message(self, fmt: str, *args) -> None:
        say(f"{self.address_string()} {fmt % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="book-to-skill dashboard server (127.0.0.1 only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--src", default="", help="источник для первого прогона")
    parser.add_argument("--no-warmup", action="store_true", help="не прогревать каскад при старте")
    args = parser.parse_args()

    if args.src:
        with _lock:
            st = load_state()
            st["src"] = args.src
            save_state(st)

    # Новая сессия — чистый лог: по нему видно, что страница в панели реально ходит.
    try:
        LOG_FILE.write_text("", encoding="utf-8")
    except OSError:
        pass
    say(f"старт: --port {args.port}")

    if not args.no_warmup:
        with _lock:
            st = load_state()
            if not st.get("report"):
                say("прогрев: первый прогон каскада…")
                say("  " + summary_line(run_fetch(st)))

    # allow_reuse_address на Windows разрешает ПОВТОРНЫЙ bind на тот же порт — тогда запросы
    # обслуживает случайный из процессов (симптом: сервер отвечает то новым кодом, то старым,
    # а страница «то работает, то нет»). Запрещаем: второй экземпляр должен честно упасть.
    ThreadingHTTPServer.allow_reuse_address = False
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as e:
        say(f"ОШИБКА: порт {args.port} занят ({e}) - сервер, похоже, уже запущен")
        print(f"порт {args.port} уже занят: {e}", flush=True)
        return 1
    say(f"b2s dashboard: http://127.0.0.1:{args.port}/  (pid {os.getpid()}) - жду страницу")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлен", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
