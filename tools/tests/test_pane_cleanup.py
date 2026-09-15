#!/usr/bin/env python3
"""Уборка рабочего каталога в панели: список, «убрать», автоуборка архива.

Зачем: черновик живёт каталогом `staging/<слаг>`, и после установки он оставался
второй копией скилла - навсегда; сырьё в `b2s_fetched/` копилось так же молча.
Правила выбраны владельцем: установленный черновик = архив с TTL, свежие держим
лимитом, служебные `_probe*` не трогаем, и ни одно удаление не молчит.

Что проверяем
-------------
1. Четыре функции уборки извлекаются из ЖИВОГО `plugin.js` (не копия): разойдётся
   файл - разойдётся и тест;
2. `loadDrafts` ходит в ядро телом (`POST /drafts`) и кладёт ответ в состояние;
3. автоуборка при открытии шлёт `keep: 99` - она трогает ТОЛЬКО архив по TTL и
   оставляет лимит свежих в покое (иначе автоуборка снесла бы недоделанный
   черновик чужого источника);
4. убранное называется в статусе по именам, а пустой ответ молчит (не врёт
   «убрано 0»);
5. `runPrune` идёт с полными правилами ядра (`apply: true`, без своего лимита) и
   печатает имена; при пустом плане честно говорит «убирать нечего»;
6. `runDrop` шлёт ключ и источник, при отказе ядра показывает ЕГО причину
   (например, «служебный каталог не убираем»), и на время работы гасит кнопки;
7. в разметке блок 3 действительно рисует строку уборки, а `/state` наполняет её.
"""
from __future__ import annotations

import json
import os
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
        print(f"  OK   {name}" + (f" - {note}" if note else ""))
    else:
        print(f"  FAIL {name}" + (f" - {detail}" if detail else ""))


def cut_function(src: str, decl: str) -> str:
    """Вырезать ``const NAME = async (…) => { … }`` по балансу фигурных скобок."""
    start = src.index(decl)
    eq = src.index("=", start)
    expr_start = eq + 1
    while src[expr_start] in " \t\r\n":
        expr_start += 1
    # Значение может начинаться с `async`: без него тело с `await` не соберётся.
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
const SRC = fs.readFileSync(process.env.CLEAN_SRC, 'utf8')
const factory = new Function(
  'ctx', 'setDrafts', 'setBusy', 'setTone', 'say', 'note', 'loadDraft', 'src',
  'return (() => {\n' + SRC + '\nreturn { loadDrafts, pruneArchive, runPrune, runDrop }\n})()'
)
const out = {}

function harness(answer, srcValue) {
  const state = { calls: [], drafts: [], busy: [], tone: [], status: [], refreshed: 0 }
  const ctx = {
    rest: async (path, opts) => {
      state.calls.push({ path, body: (opts && opts.body) || null })
      return typeof answer === 'function' ? answer(path) : answer
    }
  }
  const api = factory(
    ctx,
    (v) => state.drafts.push(v),
    (v) => state.busy.push(v),
    (v) => state.tone.push(v),
    (v) => state.status.push(v),
    (e) => 'ОШИБКА:' + (e && e.message ? e.message : String(e)),
    async () => { state.refreshed++ },
    srcValue === undefined ? 'https://example.org/page' : srcValue
  )
  return { api, state }
}

/* 1. Список черновиков: телом, ответ - в состояние. */
{
  const { api, state } = harness({
    ok: true, dirs: [{ key: 'aa', files: 3 }], others: [{ key: 'aa', files: 3 }],
    droppable: ['ee'], keep: 5, ttl_days: 7
  })
  await api.loadDrafts()
  out.load = {
    path: state.calls[0] && state.calls[0].path,
    body: state.calls[0] && state.calls[0].body,
    got: state.drafts.length
  }
}

/* 2. Автоуборка при открытии: keep 99 = «только архив», убранное названо. */
{
  const { api, state } = harness((path) => path === '/prune'
    ? { ok: true, dropped: ['ee-inst-old'] }
    : { ok: true, dirs: [], others: [], droppable: [] })
  await api.pruneArchive('key-x')
  const prune = state.calls.find((c) => c.path === '/prune')
  out.archive = {
    path: prune && prune.path,
    body: prune && prune.body,
    status: state.status,
    listed: state.calls.some((c) => c.path === '/drafts')
  }
}

/* 3. Пустой ответ автоуборки не рождает ложного «убрано». */
{
  const { api, state } = harness({ ok: true, dropped: [] })
  await api.pruneArchive('key-y')
  out.archiveSilent = state.status
}

/* 4. Ручная уборка: полные правила ядра, имена в статусе. */
{
  const { api, state } = harness({ ok: true, dropped: ['bb', 'cc'] })
  await api.runPrune()
  const prune = state.calls.find((c) => c.path === '/prune')
  out.prune = { body: prune && prune.body, status: state.status, busy: state.busy }
}

/* 5. Ручная уборка, когда убирать нечего. */
{
  const { api, state } = harness({ ok: true, dropped: [] })
  await api.runPrune()
  out.pruneEmpty = state.status
}

/* 6. Убрать один каталог - и услышать причину отказа от ядра. */
{
  const { api, state } = harness({ ok: true, dropped: 'introduction', files: 3, source_files: ['introduction.md'] })
  await api.runDrop('introduction')
  const drop = state.calls.find((c) => c.path === '/drop')
  out.drop = { body: drop && drop.body, status: state.status, refreshed: state.refreshed, busy: state.busy }
}
{
  const { api, state } = harness({ ok: false, error: 'служебный каталог _probe_x не убираем: на нём стоят тесты ядра' })
  await api.runDrop('_probe_x')
  out.dropRefused = { tone: state.tone, status: state.status, busy: state.busy }
}

/* 7. Ошибка REST не роняет панель: говорим причину текстом. */
{
  const { api, state } = harness(() => { throw new Error('ядро не ответило') })
  await api.runDrop('bb')
  out.dropError = { tone: state.tone, status: state.status }
}

console.log(JSON.stringify(out))
"""


def main() -> int:
    if not PLUGIN.is_file():
        print(f"нет файла плагина: {PLUGIN}")
        return 1
    src = PLUGIN.read_text(encoding="utf-8")

    bodies = {}
    for decl in ("  const loadDrafts = ", "  const pruneArchive = ",
                 "  const runPrune = ", "  const runDrop = "):
        try:
            bodies[decl] = cut_function(src, decl)
        except ValueError as exc:
            check(f"{decl.strip()} извлекается из plugin.js", False, str(exc))
            bodies[decl] = ""
    check("четыре функции уборки извлекаются из живого plugin.js",
          all(len(b) > 120 for b in bodies.values()),
          ", ".join(f"{d.split()[1]}={len(b)}" for d, b in bodies.items()),
          note="живой код, не копия")
    # Панель обязана наполнять строку уборки: список - из /state, архив - тихо.
    check("mount-эффект берёт список из /state",
          "setDrafts(s.drafts)" in src)
    check("mount-эффект запускает автоуборку архива — активный источник берёт из ПОЛЯ "
          "(черновик прошлого прогона больше не защищается как «текущий»)",
          "pruneArchive(stored.src || '')" in src)
    check("блок 3 рисует строку уборки после раскладки по главам",
          "chapterSlot,\n          cleanupSlot" in src)
    check("кнопка-корзина называет действие и каталог",
          "'убрать рабочий каталог ' + d.key" in src)
    check("тултип «убрать лишнее» есть в TIP",
          "prune: 'Убрать лишние рабочие каталоги" in src)

    if not all(bodies.values()):
        return 1

    # Склейка с именами: `cut_function` отдаёт только значение (`async () => {…}`),
    # поэтому объявление возвращаем на место - иначе в фабрике не будет имён.
    snippet = "\n".join(
        "const " + d.split("const ")[1].split(" = ")[0] + " = " + b
        for d, b in bodies.items()
    )
    with tempfile.TemporaryDirectory(prefix="b2s-pane-cleanup-") as tmp:
        tmpd = Path(tmp)
        (tmpd / "cleanup.js").write_text(snippet, encoding="utf-8")
        (tmpd / "run.mjs").write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ, CLEAN_SRC=str(tmpd / "cleanup.js"))
        env["HERMES_HOME"] = env.get("HERMES_HOME", "")
        proc = subprocess.run(["node", "run.mjs"], cwd=str(tmpd), env=env,
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr)
            check("runner уборки исполнился в node", False, f"код {proc.returncode}")
            return 1
        out = json.loads(proc.stdout.strip().splitlines()[-1])

    check("listDrafts: список идёт телом POST /drafts с источником",
          out["load"]["path"] == "/drafts"
          and out["load"]["body"] == {"src": "https://example.org/page"}
          and out["load"]["got"] == 1,
          json.dumps(out["load"], ensure_ascii=False))

    arch = out["archive"]
    check("автоуборка: только архив (keep 99, срок ядра, apply)",
          arch["body"] == {"src": "key-x", "keep": 99, "days": 0, "apply": True},
          json.dumps(arch["body"], ensure_ascii=False))
    check("автоуборка: убранное названо в статусе",
          any("ee-inst-old" in s for s in arch["status"]),
          json.dumps(arch["status"], ensure_ascii=False))
    check("автоуборка: список черновиков перечитан",
          arch["listed"])
    check("автоуборка: пустой ответ не врёт «убрано»",
          out["archiveSilent"] == [])

    check("ручная уборка: полные правила ядра и имена в статусе",
          out["prune"]["body"] == {"src": "https://example.org/page", "apply": True}
          and any("bb" in s and "cc" in s for s in out["prune"]["status"]),
          json.dumps(out["prune"], ensure_ascii=False))
    check("ручная уборка: кнопки гаснут на время работы",
          out["prune"]["busy"] == ["prune", ""],
          json.dumps(out["prune"]["busy"]))
    check("ручная уборка: пустой план говорит «убирать нечего»",
          any("нечего" in s for s in out["pruneEmpty"]),
          json.dumps(out["pruneEmpty"], ensure_ascii=False))

    drop = out["drop"]
    check("drop: ключ и источник уходят в ядро",
          drop["body"] == {"key": "introduction", "src": "https://example.org/page"},
          json.dumps(drop["body"], ensure_ascii=False))
    check("drop: названо число файлов и унесённое сырьё",
          any("introduction" in s and "3 файлов" in s and "introduction.md" in s for s in drop["status"]))
    check("drop: сводка черновика перечитана после уборки", drop["refreshed"] == 1)
    check("drop: кнопки гаснут на время работы", drop["busy"] == ["drop", ""])

    refused = out["dropRefused"]
    check("drop: отказ ядра показан ЕГО словами",
          "error" in refused["tone"] and any("служебный каталог" in s for s in refused["status"]),
          json.dumps(refused, ensure_ascii=False))
    check("drop: после отказа кнопки снова живые", refused["busy"] == ["drop", ""])

    err = out["dropError"]
    check("drop: падение REST названо причиной, а не молчанием",
          "error" in err["tone"] and any("ОШИБКА" in s for s in err["status"]),
          json.dumps(err, ensure_ascii=False))

    failed = [n for n, ok in checks if not ok]
    print(f"\nпроверок: {len(checks)}, провалов: {len(failed)}")
    if failed:
        print("провалы: " + "; ".join(failed))
        return 1
    print("ВСЁ ЗЕЛЁНОЕ: уборка не молчит и не трогает лишнего")
    return 0


if __name__ == "__main__":
    sys.exit(main())
