#!/usr/bin/env python3
"""Шаг 1 «Источник»: успех = источник реально есть (файл на диске / живой URL).

Правило владельца, дословно: «в группе 1 успешным является, если указанный в поле
файл или url существует/доступен. В противном случае - провал».

Почему это отдельная проверка, а не следствие разбора. Долго считалось, что блок 1
зеленеет «за разбор»: граница держалась на признаке ``analyzed``. Тогда любой ввод
получал зелёный цвет от чужого удачного отчёта ядра - владелец видел зелёную границу
на голой «1» и справедливо спрашивал: «это что - найденный валидный файл на диске, но
тогда на каком?».

Теперь цвет границы блока 1 берётся у ПРОБЫ источника (``serve.source_status``),
которая отвечает без разбора:

* файл есть на диске → успех;
* URL отвечает (HEAD, при 403/405 - GET) → успех;
* «1», отсутствующий файл, каталог, молчащий сайт → провал, и причина словами.

Проверки
--------
1. «1» - не источник: ни URL, ни путь;
2. файла нет по пути - провал с этим путём в причине;
3. файл есть (настоящий временный файл) - успех;
4. каталог вместо файла - провал;
5. пустое поле - провал;
6. живой URL (локальный сервер, HTTP 200) - успех;
7. ответ 404 - провал;
8. недоступный хост - провал, причина не пустая;
9. ``api.do_probe`` - та же логика, что у ядра (одна правда, не две);
10. пробу видно снаружи: маршрут ``/probe`` у плагина, ``/api/probe`` у сервера,
    ``probe`` в CLI и вызов из панели.

Сеть в тесте - только своя: локальный HTTP-сервер на 127.0.0.1, никаких внешних
сайтов, поэтому проверка детерминирована и работает без интернета.
"""
from __future__ import annotations

import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import api  # noqa: E402
import serve  # noqa: E402


class _Page(BaseHTTPRequestHandler):
    """Мини-сайт: /ok - 200, всё остальное - 404."""

    def _answer(self, with_body: bool = True) -> None:
        code = 200 if self.path.startswith("/ok") else 404
        body = b"<html><body>ok</body></html>" if code == 200 else b"nope"
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._answer()

    def do_HEAD(self) -> None:  # noqa: N802
        self._answer(with_body=False)

    def log_message(self, *args) -> None:  # noqa: D102 - тишина в выводе теста
        pass


def main() -> int:
    checks: list[tuple[str, bool]] = []

    def check(name: str, ok: bool, why: str = "") -> None:
        checks.append((name, bool(ok)))
        print(("  OK   " if ok else "  FAIL ") + name + ("" if ok else f"   <- {why}"))

    tmp = Path(tempfile.mkdtemp(prefix="b2s_probe_"))
    real_md = tmp / "guide.md"
    real_md.write_text("# guide\n\ntext\n", encoding="utf-8")
    ghost = tmp / "ghost.md"

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    print("источник - не источник:")
    out = serve.source_status("1")
    check("«1» - провал (ни URL, ни путь)",
          out["ok"] is False and out["kind"] == "none" and "не похоже ни на URL" in out["detail"],
          str(out))

    out = serve.source_status(str(ghost))
    check("отсутствующий файл - провал, путь назван",
          out["ok"] is False and out["kind"] == "path" and str(ghost).replace("\\", "/").split("/")[-1] in out["detail"],
          str(out))

    out = serve.source_status("")
    check("пустое поле - провал", out["ok"] is False and out["kind"] == "empty", str(out))

    out = serve.source_status(str(tmp))
    check("каталог вместо файла - провал",
          out["ok"] is False and out["kind"] == "dir" and "каталог" in out["detail"], str(out))

    print("\nфайл на диске:")
    out = serve.source_status(str(real_md))
    check("существующий файл - успех",
          out["ok"] is True and out["kind"] == "file" and real_md.name in out["detail"], str(out))

    print("\nURL:")
    out = serve.source_status(base + "/ok")
    check("живой URL - успех (HTTP 200 в причине)",
          out["ok"] is True and out["kind"] == "url" and "200" in out["detail"], str(out))

    out = serve.source_status(base + "/missing")
    check("URL ответил 404 - провал",
          out["ok"] is False and out["kind"] == "url" and "404" in out["detail"], str(out))

    out = serve.source_status("http://no-such-host-b2s-probe.invalid/")
    check("недоступный хост - провал с непустой причиной",
          out["ok"] is False and out["kind"] == "url" and len(out["detail"]) > len("URL недоступен"), str(out))
    srv.shutdown()

    print("\nодна правда, видная снаружи:")
    check("api.do_probe зовёт ядро, а не свою копию правил",
          api.do_probe(str(real_md)) == serve.source_status(str(real_md)),
          "у панели и ядра разъедутся ответы о доступности источника")

    serve_src = (REPO / "tools" / "serve.py").read_text(encoding="utf-8")
    check("сервер отдаёт POST /api/probe", '"/api/probe"' in serve_src and "source_status(" in serve_src)
    check("сервер берёт опознание источника из общего контракта, а не копией",
          "from book_to_skill.plugins.base import USER_AGENT, looks_like_path" in serve_src,
          "своя копия правил «это путь или url» разъедется с плагинами")

    api_src = (REPO / "tools" / "api.py").read_text(encoding="utf-8")
    check("в CLI ядра есть probe (--src)", '"probe"' in api_src and 'args.cmd == "probe"' in api_src)

    plugin_files = [
        Path("D:/NEURO/Hermes/data/hermes/plugins/b2s/dashboard/plugin_api.py"),
        REPO / "hermes" / "plugins" / "b2s" / "dashboard" / "plugin_api.py",
    ]
    for pf in plugin_files:
        src = pf.read_text(encoding="utf-8")
        check(f"плагин отдаёт POST /probe ({pf.parts[0][:1]}:{'профиль' if 'NEURO' in str(pf) else 'зеркало'})",
              'class ProbeBody' in src and '@router.post("/probe")' in src and "do_probe(body.src)" in src,
              "панель не сможет спросить о доступности источника")

    pane = (REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js").read_text(encoding="utf-8")
    check("панель спрашивает пробу при вводе источника",
          "ctx.rest('/probe'" in pane and "setSrcProbe(out)" in pane,
          "нет вызова пробы - граница блока 1 останется без критерия")

    bad = [n for n, ok in checks if not ok]
    print(f"\nпроверок: {len(checks)}, провалов: {len(bad)}")
    if bad:
        print("провалено: " + "; ".join(bad))
        return 1
    print("ВСЁ ЗЕЛЁНОЕ: доступность источника - единственный критерий шага 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
