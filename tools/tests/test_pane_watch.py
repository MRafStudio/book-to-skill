#!/usr/bin/env python3
"""Вотчер панели: refresh после действия, ушедшего в чат.

Зачем: описание категории сочиняет LLM, поэтому клик в панели уходит интентом в
сессию, а результат на диске появляется через минуты. Панель о нём не узнаёт —
файл создан, а рамка блока осталась красной и кнопка «написать» на месте
(владелец: «ты не сделал последний шаг — не перечитал текущую категорию»).

Что проверяем
-------------
1. ``watchDesc`` извлекается из ЖИВОГО ``plugin.js`` (не копия) — баланс скобок
   вместо ручного пересказа кода: разойдётся файл, разойдётся и тест;
2. состояние цели изменилось → панель перечитывает снимок ядра ЦЕЛИКОМ
   (список категорий, ``details``, ``loose``) и рапортует «записано»;
3. следим за именем цели: в снимке обновляется именно ``networking``, даже если
   в выпадашке к этому моменту выбрана другая категория;
4. состояние не изменилось → опрос прекращается по дедлайну с честным текстом
   («смотри ответ агента в чате»), а не висит молча и не крутится вечно;
5. интенты ``desc`` / ``desc-fix`` / ``desc-rewrite`` этот вотчер запускают — иначе он мёртвый код.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    if ok:
        print(f"  OK   {name}" + (f" — {note}" if note else ""))
    else:
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


def cut_function(src: str, decl: str) -> str:
    """Вырезать ``const NAME = (…) => { … }`` из файла по балансу фигурных скобок.

    Ищем объявление, затем первую ``{`` после стрелки и считаем глубину. Скобки в
    строках и комментариях считаются тоже — для нашего кода это безвредно, но
    проверку на непустой результат и наличие ``return (`` оставляем.
    """
    start = src.index(decl)
    eq = src.index("=", start)
    expr_start = src.index("(", eq)      # начало значения: (targetCat, before) => {…}
    arrow = src.index("=>", expr_start)
    open_brace = src.index("{", arrow)
    depth = 0
    for i in range(open_brace, len(src)):
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[expr_start:i + 1]
    raise ValueError("не найден конец функции: " + decl)


RUNNER = r"""
import fs from 'node:fs'
const SRC = fs.readFileSync(process.env.WATCH_SRC, 'utf8')
const factory = new Function(
  'ctx', 'setCats', 'setCatMeta', 'setCatLoose', 'setTone', 'say', 'note',
  'setInterval', 'clearInterval', 'Date',
  'return (' + SRC + ')'
)
const out = {}

/* Прогон 1: цель была без файла, ядро на третьем тике отдаёт desc_state: ok. */
{
  const seen = { cats: null, meta: null, loose: null, tone: [], status: [], stopped: false }
  const frames = [
    { categories: ['networking', 'devops'], details: { networking: { desc_state: 'no-file', skills: 0 } }, loose: [] },
    { categories: ['networking', 'devops'], details: { networking: { desc_state: 'no-file', skills: 0 } }, loose: [] },
    { categories: ['networking', 'devops', 'web'],
      details: { networking: { desc_state: 'ok', desc: 'Network connectivity…', skills: 0 } }, loose: [{ name: 'x' }] }
  ]
  let cb = null, cleared = 0
  const watch = factory(
    { rest: async () => JSON.parse(JSON.stringify(frames.shift() || frames.at(-1))) },
    (v) => { seen.cats = v }, (v) => { seen.meta = v }, (v) => { seen.loose = v },
    (v) => seen.tone.push(v), (v) => seen.status.push(v), String,
    (fn) => { cb = fn; return 1 }, () => { cleared++ }, Date
  )
  watch('networking', 'no-file')
  out.hasCb = typeof cb === 'function'
  for (let i = 0; i < 3; i++) await cb()
  await cb()   // лишний тик: после успеха опрос должен молчать
  out.afterSuccess = {
    cats: seen.cats, descState: seen.meta && seen.meta.networking && seen.meta.networking.desc_state,
    loose: seen.loose, tone: seen.tone, status: seen.status, cleared
  }
}

/* Прогон 2: состояние не меняется — дедлайн обязан остановить опрос и сказать правду. */
{
  const seen = { status: [], tone: [], cleared: 0 }
  let cb = null
  let now = 1_000_000
  const frozen = { categories: ['networking'], details: { networking: { desc_state: 'no-file' } }, loose: [] }
  const watch = factory(
    { rest: async () => JSON.parse(JSON.stringify(frozen)) },
    () => {}, () => {}, () => {},
    (v) => seen.tone.push(v), (v) => seen.status.push(v), String,
    (fn) => { cb = fn; return 1 }, () => { seen.cleared++ },
    { now: () => now }
  )
  watch('networking', 'no-file')
  await cb()                       // тик 1: пусто, до дедлайна далеко
  const statusAfterFirst = seen.status.length
  now += 200_000                   // проматываем три минуты
  await cb()                       // тик 2: дедлайн
  out.afterDeadline = {
    statusAfterFirst, status: seen.status, tone: seen.tone, cleared: seen.cleared
  }
}

/* Прогон 3: пустая цель не должна поднимать опрос вовсе. */
{
  let calls = 0, cb = null
  const watch = factory(
    { rest: async () => { calls++; return {} } },
    () => {}, () => {}, () => {}, () => {}, () => {}, String,
    (fn) => { cb = fn; return 1 }, () => {}, Date
  )
  watch('', 'no-file')
  out.emptyTarget = { calls, hasCb: typeof cb === 'function' }
}

console.log(JSON.stringify(out))
"""


def main() -> int:
    if not PLUGIN.is_file():
        print(f"нет файла плагина: {PLUGIN}")
        return 1
    src = PLUGIN.read_text(encoding="utf-8")

    try:
        body = cut_function(src, "  const watchDesc = ")
    except ValueError as exc:
        check("watchDesc извлекается из plugin.js", False, str(exc))
        body = ""
    else:
        check("watchDesc извлекается из plugin.js", "--" not in body and len(body) > 400,
              f"длина {len(body)}", note=f"{len(body)} символов живого кода")

    if not body:
        return 1

    with tempfile.TemporaryDirectory(prefix="b2s-pane-watch-") as tmp:
        tmpd = Path(tmp)
        (tmpd / "watch.js").write_text(body, encoding="utf-8")
        (tmpd / "run.mjs").write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ)          # node без системных переменных валит CSPRNG
        env["WATCH_SRC"] = str(tmpd / "watch.js")
        proc = subprocess.run(
            ["node", str(tmpd / "run.mjs")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )
    if proc.returncode != 0:
        check("вотчер выполняется в node", False, (proc.stderr or proc.stdout)[-600:])
        return 1
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    check("вотчер выполняется в node", True, note="моки ядра, без приложения")

    a = out["afterSuccess"]
    check("после смены состояния панель перечитывает категории", bool(a["cats"]),
          f"cats={a['cats']!r}", note=", ".join(a["cats"] or []))
    check("обновляется ИМЕННО цель (networking → ok)", a["descState"] == "ok",
          f"desc_state={a['descState']!r}")
    check("снимок ядра читается целиком (loose-скиллы тоже)", a["loose"] == [{"name": "x"}],
          f"loose={a['loose']!r}")
    check("панель рапортует «записано», а не молчит",
          any("записано" in s for s in a["status"]), f"status={a['status']!r}")
    check("успех гасит тон в done", a["tone"] == ["done"], f"tone={a['tone']!r}")
    check("успех останавливает опрос", a["cleared"] == 1, f"clearInterval вызван {a['cleared']} раз")
    check("лишний тик после успеха ничего не печатает", len(a["status"]) == 1,
          f"status={a['status']!r}")

    b = out["afterDeadline"]
    check("до дедлайна опрос молчит", b["statusAfterFirst"] == 0,
          f"status={b['status']!r}")
    check("дедлайн останавливает опрос", b["cleared"] == 1, f"clearInterval {b['cleared']} раз")
    check("дедлайн говорит правду про чат", any("3 минуты" in s and "чат" in s for s in b["status"]),
          f"status={b['status']!r}")

    check("пустая цель не поднимает опрос", out["emptyTarget"]["calls"] == 0
          and not out["emptyTarget"]["hasCb"], f"{out['emptyTarget']!r}")

    # Запуск вотчера для обоих интентов — иначе он мёртвый код.
    hook = re.search(r"if \(kind === 'desc' \|\| kind === 'desc-fix' \|\| kind === 'desc-rewrite'\) \{", src)
    check("интенты desc/desc-fix/desc-rewrite запускают вотчер", bool(hook),
          "в sendIntent нет ветки запуска watchDesc")

    failed = [n for n, ok in checks if not ok]
    print()
    print(f"проверок: {len(checks)}, провалов: {len(failed)}")
    if failed:
        print("провалено: " + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
