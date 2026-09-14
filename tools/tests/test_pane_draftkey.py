#!/usr/bin/env python3
"""Ключ черновика следует за ПОЛЕМ источника, а не за прошлым прогоном ядра.

Зачем: владелец закончил скилл по одному url, ввёл в блок 1 новый — и в блоке 3
увидел файлы ПРЕЖНЕЙ работы («ввёл новый url, а в окнах содержимое предыдущего
скилла»). Черновик принадлежит источнику, но ключ панель брала из ``/state``, где
лежит ПОСЛЕДНИЙ прогон: поле уехало на новый адрес, ключ остался старый — панель
показывала чужой черновик как свой.

Что проверяем
-------------
1. ядро: ``do_resolve`` считает ключ по строке источника, локально, без сети;
2. ключ нового url НЕ совпадает с ключом прежнего (это и был баг), а пустая строка
   даёт пустой ключ — панель не ищет черновик «вообще нигде»;
3. панель: ключ спрашивается у ядра по вводу (дебаунс), в тело уходит САМА строка
   поля; ядро промолчало — ключ не затираем;
4. смена ключа снимает сводку и текст черновика, план записи и статус установки;
5. вотчер и чтение сводки держатся за КЛЮЧ (не за имя) и читают источник через ref;
6. ``/state`` больше не подсовывает черновик прошлого прогона;
7. имя скилла подставляется ПОСЛЕ разбора и только пока поле не правили руками.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js"
API = REPO / "tools" / "api.py"
PLUGIN_API = REPO / "hermes" / "plugins" / "b2s" / "dashboard" / "plugin_api.py"

OLD_URL = ("https://docs.rkeeper.ru/rk7/latest/ru/"
           "rekomendatsii-po-polucheniyu-spravochnikov-iz-r_keeper-186253740.html")
NEW_URL = "https://docs.rkeeper.ru/rk7/latest/ru/novaya-stranica-987654321.html"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    if ok:
        print(f"  OK   {name}" + (f" — {note}" if note else ""))
    else:
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


def cut_between(src: str, start: str, end: str) -> str:
    """Тело эффекта из ЖИВОГО plugin.js: от маркера до маркера (без него).

    Тест не пересказывает код: разойдётся файл — разойдётся и проверка.
    Маркер конца — служебная строка эффекта (`}, [src])`), её в тело не берём.
    """
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


RUNNER = r"""
import fs from 'node:fs'
const RESOLVE = fs.readFileSync(process.env.RESOLVE_SRC, 'utf8')
const RESET = fs.readFileSync(process.env.RESET_SRC, 'utf8')
const out = {}

/* Прогон 1: ключ следует за ПОЛЕМ. Ядро отвечает по строке источника. */
{
  const calls = []
  const delays = []
  let pending = null
  const keys = []
  const ctx = { rest: async (path, opts) => {
    calls.push({ path: path, src: (opts.body || {}).src })
    return { ok: true, key: 'slug-' + String(opts.body.src).split('/').pop() }
  } }
  const run = new Function('ctx', 'src', 'setDraftKey', 'setTimeout', 'clearTimeout', RESOLVE)
  const fire = (src) => {
    pending = null
    run(ctx, src, (v) => keys.push(v), (fn, ms) => { delays.push(ms); pending = fn; return 1 }, () => {})
    return pending
  }
  /* fire возвращает колбэк, который поставил setTimeout: дожидаемся его явно,
     иначе `await fire(url)` дождался бы самой функции, а не её работы. */
  const drain = async (src) => {
    const cb = fire(src)
    if (cb) await cb()
    return !!cb
  }
  await drain('https://docs.rkeeper.ru/rk7/latest/ru/186253740.html')
  await drain('https://docs.rkeeper.ru/rk7/latest/ru/novaya-stranica-987654321.html')
  /* Пустая строка: ключ снимается, в ядро не ходим вовсе. */
  const emptyCb = await drain('')
  const callsBeforeEmpty = calls.length
  /* Ядро промолчало: ключ обязан остаться прежним, а не обнулиться. */
  const boom = new Function('ctx', 'src', 'setDraftKey', 'setTimeout', 'clearTimeout', RESOLVE)
  const keysBoom = []
  let boomCb = null
  boom({ rest: async () => { throw new Error('ядро недоступно') } },
       'https://example.com/x.html', (v) => keysBoom.push(v),
       (fn) => { boomCb = fn; return 1 }, () => {})
  await boomCb()
  out.resolve = { keys, calls, delays, emptyCb: !!emptyCb, callsBeforeEmpty, keysBoom }
}

/* Прогон 2: смена ключа снимает всё, что относилось к прежнему источнику. */
{
  const run = new Function('draftKey', 'setDraft', 'setDraftText', 'setDraftFile',
                           'setInstalled', 'dropPreview', 'loadDraft', 'loadDrafts', RESET)
  const seen = { calls: [], reads: 0 }
  const rec = (name) => (v) => seen.calls.push(name + '=' + JSON.stringify(v === undefined ? null : v))
  run('novaya-stranica', rec('draft'), rec('draftText'), rec('draftFile'),
      rec('installed'), () => seen.calls.push('dropPreview'), () => { seen.reads++ },
      () => { seen.reads++ })
  out.resetFilled = seen
  const seen2 = { calls: [], reads: 0 }
  const rec2 = (name) => (v) => seen2.calls.push(name)
  run('', rec2('draft'), rec2('draftText'), rec2('draftFile'), rec2('installed'),
      () => seen2.calls.push('dropPreview'), () => { seen2.reads++ }, () => { seen2.reads++ })
  out.resetEmpty = seen2
}

console.log(JSON.stringify(out))
"""


def core() -> object:
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import api  # noqa: PLC0415 — ядро поднимаем по месту, как это делает REST-обёртка
    return api


def main() -> int:
    if not PLUGIN.is_file() or not API.is_file():
        print(f"нет файлов: {PLUGIN} / {API}")
        return 1

    # --- 1-2. Ядро: ключ по строке источника, без сети -----------------------
    api = core()
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("сеть не нужна")):
        old = api.do_resolve(OLD_URL)
        new = api.do_resolve(NEW_URL)
        empty = api.do_resolve("")

    check("ядро: /resolve отвечает на новый url", bool(new.get("ok")), f"{new!r}")
    check("ядро: ключ нового url отличается от ключа прежнего",
          old.get("key") and new.get("key") and old["key"] != new["key"],
          f"old={old.get('key')!r} new={new.get('key')!r}",
          note=f"новый ключ: {new.get('key')}")
    check("ядро: ключ считается по строке источника (новый url узнаётся)",
          "novaya-stranica" in (new.get("key") or ""), f"key={new.get('key')!r}")
    check("ядро: пустая строка даёт пустой ключ",
          empty.get("key") == "" and empty.get("ok") is True, f"{empty!r}")
    check("ядро: предложенное имя не пустое (эвристика по url)",
          bool(new.get("suggested_name")), f"{new.get('suggested_name')!r}")

    src = PLUGIN.read_text(encoding="utf-8")
    rest = PLUGIN_API.read_text(encoding="utf-8")

    # --- 3-4. Панель: эффекты живьём в node ---------------------------------
    try:
        body_resolve = cut_between(src, "const asked = (src || '').trim()", "}, [src])")
        body_reset = cut_between(
            src,
            "setDraft(null); setDraftText(null); setDraftFile(''); setInstalled(null)",
            "}, [draftKey])")
    except ValueError as exc:
        check("эффекты ключа извлекаются из plugin.js", False, str(exc))
        return 1

    check("эффекты ключа извлекаются из plugin.js",
          len(body_resolve) > 300 and len(body_reset) > 120,
          f"resolve={len(body_resolve)} reset={len(body_reset)} символов")

    with tempfile.TemporaryDirectory(prefix="b2s-pane-draftkey-") as tmp:
        tmpd = Path(tmp)
        (tmpd / "resolve.js").write_text(body_resolve, encoding="utf-8")
        (tmpd / "reset.js").write_text(body_reset, encoding="utf-8")
        (tmpd / "run.mjs").write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ)          # node без системных переменных валит CSPRNG
        env["RESOLVE_SRC"] = str(tmpd / "resolve.js")
        env["RESET_SRC"] = str(tmpd / "reset.js")
        proc = subprocess.run(["node", str(tmpd / "run.mjs")], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=120, env=env)
    if proc.returncode != 0:
        check("эффекты ключа выполняются в node", False, (proc.stderr or proc.stdout)[-600:])
        return 1
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    check("эффекты ключа выполняются в node", True, note="моки ядра, без приложения")

    r = out["resolve"]
    check("панель: ключ следует за полем — новый url даёт новый ключ",
          len(r["keys"]) >= 2 and r["keys"][0] != r["keys"][1], f"keys={r['keys']!r}")
    check("панель: в ядро уходит САМА строка поля (не прежняя)",
          r["calls"] and r["calls"][-1]["src"].endswith("novaya-stranica-987654321.html"),
          f"calls={r['calls']!r}")
    check("панель: ключ спрашивается по вводу, с дебаунсом",
          r["delays"] and all(d == 500 for d in r["delays"]), f"delays={r['delays']!r}")
    check("панель: пустая строка снимает ключ и не дёргает ядро",
          r["emptyCb"] is False and len(r["calls"]) == r["callsBeforeEmpty"],
          f"calls={len(r['calls'])} до пустого: {r['callsBeforeEmpty']}")
    check("панель: молчание ядра не затирает ключ (не врём «черновика нет»)",
          r["keysBoom"] == [] and True, f"setDraftKey={r['keysBoom']!r}")

    f = out["resetFilled"]
    called = " ".join(f["calls"])
    check("панель: смена ключа снимает сводку черновика", "draft=null" in called, called)
    check("панель: смена ключа снимает текст черновика", "draftText=null" in called, called)
    check("панель: смена ключа снимает план записи и статус установки",
          "installed=null" in called and "dropPreview" in called, called)
    check("панель: по новому ключу сводка читается заново", f["reads"] == 2, f"reads={f['reads']}")
    e = out["resetEmpty"]
    check("панель: пустой ключ тоже снимает чужое, но ядро не дёргает",
          "dropPreview" in e["calls"] and e["reads"] == 0, f"{e!r}")

    # --- 5-7. Замыкания и подстановка имени (по живому коду) ----------------
    check("вотчер держится за КЛЮЧ источника, а не за имя скилла",
          "}, [draftOpen, draftKey])" in src, "в plugin.js не найдены зависимости [draftOpen, draftKey]")
    check("сводка и текст черновика читают источник через ref (не замыкание)",
          "body: { name: nameRef.current, src: srcRef.current }" in src,
          "в /draft уходит не srcRef.current")
    check("/state больше не подсовывает черновик прошлого прогона",
          "if (alive && s && s.draft) setDraft(" not in src
          and "if (alive && s && s.draft_key) setDraftKey(" not in src,
          "в mount-эффекте остался setDraft/setDraftKey из /state")
    check("уборка архива знает активный источник из поля, а не из прошлого прогона",
          "pruneArchive(stored.src || '')" in src, "в pruneArchive уходит прежний ключ")
    check("имя подставляется после разбора",
          "out.suggested_name && !nameTouched.current" in src,
          "в runRerun нет подстановки имени")
    check("ручная правка имени закрывает подстановку навсегда",
          "nameTouched.current = true" in src and "setNameAuto(false)" in src,
          "onChange поля имени не помечает ручной ввод")
    check("под полем имени сказано, что имя подставлено",
          "подставлено по источнику" in src, "нет подписи про подстановку")
    check("поле источника больше не открывается чужим примером",
          "useState(stored.src || '')" in src, "в useState источника остался хардкод-пример")
    check("пример url убран из кода панели", "docs.python.org/3/library/pathlib.html" not in src,
          "хардкод-пример всё ещё в plugin.js")

    # --- REST-дверь ядра ----------------------------------------------------
    check("REST: маршрут POST /resolve объявлен",
          '@router.post("/resolve")' in rest and "do_resolve(body.src)" in rest,
          "в plugin_api.py нет маршрута /resolve")
    check("REST: у /resolve есть тело запроса", "class ResolveBody(BaseModel)" in rest,
          "нет ResolveBody")
    check("CLI: подкоманда resolve доступна (для проб и тестов)",
          'sub.add_parser("resolve"' in Path(API).read_text(encoding="utf-8"),
          "в api.py нет подкоманды resolve")
    check("ядро: do_resolve объявлен и объясняет, почему ключ от поля",
          "def do_resolve(" in Path(API).read_text(encoding="utf-8"),
          "в api.py нет do_resolve")

    failed = [n for n, ok in checks if not ok]
    print()
    print(f"проверок: {len(checks)}, провалов: {len(failed)}")
    if failed:
        print("провалено: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
