#!/usr/bin/env python3
"""Детерминированное ядро book-to-skill — вызов БЕЗ участия LLM.

Зачем
-----
Загрузка источника, очистка текста, отчёт и перенос готового черновика в
``skills/<категория>/<имя>/`` — это чистая механика: тот же Python даёт тот же
результат. Гонять её через чат (LLM) бессмысленно: медленнее, дороже и
превращает кнопку панели в «событие в чате».

Поэтому механика вынесена в один модуль, который дёргают:
  * gateway-плагин Hermes (``plugins/b2s/dashboard/plugin_api.py``) — панель
    зовёт его через ``ctx.rest('/api/plugins/b2s/rerun', …)``;
  * локальный сервер ``tools/serve.py`` (ветка плана Б) — из страницы по HTTP;
  * терминал: ``python tools/api.py rerun --src … --strat auto``.

LLM остаётся там, где без него никак: написать главы черновика (проза) и
разобрать их. Это по-прежнему делается в чате.

Использование
-------------
    python tools/api.py health
    python tools/api.py rerun  --src https://… --strat auto
    python tools/api.py state
    python tools/api.py install --name python-pathlib --cat software-development
    python tools/api.py install --name python-pathlib --confirm   # реальный перенос
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import dashboard as dash  # noqa: E402
from serve import (  # noqa: E402  — логику прогона переиспользуем, не копируем
    explain_failure,
    load_state,
    run_fetch,
    save_state,
    summary_line,
)

DEFAULT_HOME = "D:/NEURO/Hermes/data/hermes"
STAGING = REPO / "staging"
# Интерпретатор для гейтов (validate/scan). Плагин задаёт его через B2S_PYTHON:
# сам dashboard запущен как hermes.exe, и sys.executable в службе — не python.
SKILL_PY = Path(os.environ.get("B2S_PYTHON") or sys.executable)


# ── помощники ────────────────────────────────────────────────────────────────
def hermes_home() -> Path:
    """Каталог профиля Hermes (там живут ``skills/<категория>/``)."""
    return Path(os.environ.get("HERMES_HOME") or DEFAULT_HOME)


def _safe_segment(value: str, what: str) -> str:
    """Имя скилла/категории без разделителей пути — защита от ``../``."""
    text = (value or "").strip()
    if not text:
        raise ValueError(f"не задано поле «{what}»")
    if any(ch in text for ch in ("/", "\\", "..", ":", "*", "?", '"', "<", ">", "|")):
        raise ValueError(f"недопустимые символы в поле «{what}»: {text!r}")
    return text


def _run(cmd: list[str], timeout: int = 180) -> dict:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, cwd=str(REPO))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "returncode": None}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "output": out[-4000:], "error": err[-2000:]}


# ── действия ─────────────────────────────────────────────────────────────────
def do_health() -> dict:
    return {
        "ok": True,
        "layer": "python-core (no LLM)",
        "repo": str(REPO),
        "staging": str(STAGING),
        "hermes_home": str(hermes_home()),
        "python": str(SKILL_PY),
        "drafts": [p.name for p in STAGING.iterdir() if p.is_dir()] if STAGING.is_dir() else [],
    }


def do_state() -> dict:
    st = load_state()
    return {
        "ok": True,
        "state": {k: v for k, v in st.items() if k not in ("report", "history")},
        "history": st.get("history") or [],
        "report_at": st.get("report_at") or "",
        "last_error": st.get("last_error"),
        "has_report": bool(st.get("report")),
    }


def do_rerun(src: str = "", strat: str = "", mode: str = "",
             name: str = "", cat: str = "", depth: str = "", lang: str = "") -> dict:
    """Прогнать каскад по источнику: загрузка + очистка + отчёт. LLM не участвует."""
    if not (src or "").strip():
        return {"ok": False, "error": "пустой источник: нужен url или локальный путь"}
    st = load_state()
    st["src"] = src.strip()
    if strat:
        st["strat"] = strat.strip()
    for key, value in (("mode", mode), ("name", name), ("cat", cat),
                       ("depth", depth), ("lang", lang)):
        if value:
            st[key] = value.strip()
    started = time.time()
    report = run_fetch(st)
    took = round(time.time() - started, 2)
    ok = bool(report.get("ok"))
    result = {
        "ok": True,                       # запрос выполнен; ниже — что вышло
        "fetch_ok": ok,
        "source": st["src"],
        "strategy": report.get("strategy"),
        "strategy_asked": st.get("strat") or "auto",
        "seconds": took,
        "report": report,
        "report_at": st.get("report_at") or "",
        "message": summary_line(report),
        "warning": None if ok else explain_failure(st["src"], st.get("strat") or "", report),
    }
    result["headings"] = report.get("headings")
    result["coverage"] = report.get("coverage")
    return result


def do_install(name: str, cat: str = "", confirm: bool = False,
               force: bool = False) -> dict:
    """Перенос готового черновика в ``skills/<категория>/<имя>/``.

    Без ``confirm`` — только предпросмотр: что именно поедет, сколько файлов и
    байт, существует ли цель. Ничего не пишется. Это защита от «кнопка сама
    поставила скилл, которого я не просил».
    """
    try:
        skill = _safe_segment(name, "имя скилла")
        category = _safe_segment(cat, "категория") if cat else dash.DEFAULT_CATEGORY
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    staging = STAGING / skill
    if not staging.is_dir():
        return {"ok": False, "error": f"нет черновика: {staging}",
                "hint": "сначала шаг 2 — «Сделать черновик» (это уже LLM, идёт в чате)"}

    target = hermes_home() / "skills" / category / skill
    files = sorted(p for p in staging.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    skill_md = staging / "SKILL.md"
    info = {
        "ok": True,
        "dry_run": not confirm,
        "name": skill,
        "category": category,
        "staging": str(staging),
        "target": str(target),
        "files": len(files),
        "bytes": total,
        "kb": round(total / 1024, 1),
        "target_exists": target.exists(),
        "has_skill_md": skill_md.is_file(),
        "top_level": sorted(p.name for p in staging.iterdir())[:20],
    }
    if not skill_md.is_file():
        return {**info, "ok": False, "error": "в черновике нет SKILL.md"}
    if not confirm:
        return info
    if target.exists() and not force:
        return {**info, "ok": False,
                "error": f"скилл уже стоит: {target} (нужен force для перезаписи)"}

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(staging, target, dirs_exist_ok=force)

    validation = _validate(target, skill_md)
    return {**info, "dry_run": False, "installed": str(target),
            "validation": validation,
            "ok": bool(validation.get("validate", {}).get("ok"))}


def _validate(target: Path, skill_md: Path) -> dict:
    """Гейты форка: линза Hermes + security-скан сгенерированного скилла."""
    validate = _run([str(SKILL_PY), str(REPO / "tools" / "validate_skill.py"),
                     str(skill_md), "--lens", "hermes"])
    scan = _run([str(SKILL_PY), str(REPO / "tools" / "scan_generated_skill.py"),
                 str(target)])
    return {"validate": validate, "scan": scan}


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="book-to-skill core (no LLM)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("health", help="живо ли ядро и где что лежит")
    sub.add_parser("state", help="состояние панели + история прогонов")

    p_rerun = sub.add_parser("rerun", help="загрузить источник и очистить текст")
    p_rerun.add_argument("--src", required=True)
    p_rerun.add_argument("--strat", default="")
    p_rerun.add_argument("--mode", default="")
    p_rerun.add_argument("--name", default="")
    p_rerun.add_argument("--cat", default="")
    p_rerun.add_argument("--depth", default="")
    p_rerun.add_argument("--lang", default="")

    p_inst = sub.add_parser("install", help="перенести черновик в skills/ (по умолчанию предпросмотр)")
    p_inst.add_argument("--name", required=True)
    p_inst.add_argument("--cat", default="")
    p_inst.add_argument("--confirm", action="store_true", help="реально писать на диск")
    p_inst.add_argument("--force", action="store_true", help="перезаписать существующий скилл")

    args = parser.parse_args(argv)
    if args.cmd == "health":
        out = do_health()
    elif args.cmd == "state":
        out = do_state()
    elif args.cmd == "rerun":
        out = do_rerun(args.src, args.strat, args.mode, args.name, args.cat,
                       args.depth, args.lang)
    else:
        out = do_install(args.name, args.cat, args.confirm, args.force)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
