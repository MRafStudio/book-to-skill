#!/usr/bin/env python3
"""Урна панели: уборка промежуточного + сброс полей.

Зачем: черновики и сырьё в staging чистятся только по TTL (7 дней) или сверх лимита,
и человек, который наигрался, ждал уборки вручную. Владелец попросил кнопку с урной в
верхнем правом углу: «очистит всё, что намусорили, без ожидания», а панель после неё
не должна показывать данные убранного черновика — остаются источник и категория.

Что проверяем
-------------
1. ``runPurgeAll`` и ``resetAfterPurge`` вырезаются из ЖИВОГО ``plugin.js`` (не копия)
   — разойдётся файл, разойдётся и тест;
2. панель зовёт ядро POST-ом на ``/purge`` (а не пересказывает уборку сама);
3. успех сбрасывает поля: ``report``, ``draft``, ``name``, ``text`` — в null/пусто;
4. источник и КАТЕГОРИЯ не сбрасываются: ``setSrc``/``setCat`` в сбросе не вызываются;
5. статус говорит, что убрано (каталогов и файлов сырья), а не молчит;
6. ошибка ядра не молчит: честный текст вместо «ничего не произошло»;
7. плашка работы LLM читается просто «работает LLM...» — без подписи задачи
   (владелец: «это может и не черновик вовсе пишется»);
8. кнопка-урна есть в шапке, зовёт ``runPurgeAll`` и несёт подсказку владельца;
9. маршрут ``/purge`` объявлен в ядре плагина и опирается на ``do_purge_all``;
10. у ядра есть CLI-команда ``purge`` (руками тоже можно).
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
API_ROUTES = REPO / "hermes" / "plugins" / "b2s" / "dashboard" / "plugin_api.py"
CORE = REPO / "tools" / "api.py"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", note: str = "") -> None:
    checks.append((name, ok))
    if ok:
        print(f"  OK   {name}" + (f" — {note}" if note else ""))
    else:
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


def cut_function(src: str, decl: str) -> str:
    """Вырезать ``const NAME = (…) => { … }`` по балансу фигурных скобок."""
    start = src.index(decl)
    eq = src.index("=", start)
    expr_start = eq + 1
    while src[expr_start] in " \t\r\n":
        expr_start += 1
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
                return src[start:i + 1]
    raise ValueError("не найден конец функции: " + decl)


RUNNER = r"""
import fs from 'node:fs'
const SRC = fs.readFileSync(process.env.PURGE_SRC, 'utf8')
const calls = []
const stub = (name) => (v) => { calls.push([name, v]) }
const forbidden = { src: 0, cat: 0 }
const factory = new Function(
  'ctx', 'setBusy', 'setTone', 'say', 'note',
  'setText', 'setTextInfo', 'setOutOpen', 'setReport', 'setFetchedSig', 'setRerunErr', 'setRerunErrKind',
  'setName', 'setStrat', 'setMode', 'setLang', 'setAct', 'setCatDesc', 'setCatErr',
  'setNameAuto', 'setDraft', 'setDraftText', 'setDraftFile', 'setDraftOpen',
  'setDrafts', 'setInstalled', 'setChapterPlan', 'setChapterOpen', 'setPreview',
  'setReadySeen', 'setNameSeen', 'setDraftWait', 'setLlmSid', 'setLlmLabel', 'onlyB',
  'setSrc', 'setCat',
  'return (function(){' + SRC + '; return { runPurgeAll, resetAfterPurge } })()'
)

function build(restImpl) {
  const map = {}
  for (const n of ['setBusy','setTone','say','setText','setTextInfo','setOutOpen','setReport',
    'setFetchedSig','setRerunErr','setRerunErrKind','setName','setStrat','setMode','setLang','setAct','setCatDesc',
    'setCatErr','setNameAuto','setDraft','setDraftText','setDraftFile','setDraftOpen','setDrafts',
    'setInstalled','setChapterPlan','setChapterOpen','setPreview','setReadySeen','setNameSeen','setDraftWait',
    'setLlmSid','setLlmLabel','onlyB']) {
    map[n] = stub(n)
  }
  map.setSrc = () => { forbidden.src++ }
  map.setCat = () => { forbidden.cat++ }
  return factory(
    { rest: restImpl },
    map.setBusy, map.setTone, map.say, String,
    map.setText, map.setTextInfo, map.setOutOpen, map.setReport, map.setFetchedSig, map.setRerunErr, map.setRerunErrKind,
    map.setName, map.setStrat, map.setMode, map.setLang, map.setAct, map.setCatDesc, map.setCatErr,
    map.setNameAuto, map.setDraft, map.setDraftText, map.setDraftFile, map.setDraftOpen,
    map.setDrafts, map.setInstalled, map.setChapterPlan, map.setChapterOpen, map.setPreview,
    map.setReadySeen, map.setNameSeen, map.setDraftWait, map.setLlmSid, map.setLlmLabel, map.onlyB,
    map.setSrc, map.setCat
  )
}

const out = {}

/* Прогон 1: ядро ответило — панель сбрасывает поля и рапортует, что убрано. */
{
  let called = null
  const api = build(async (path, opts) => {
    called = { path, method: opts && opts.method }
    return { ok: true, dirs: ['a', 'b'], source_files: ['x.md', 'y.report.json'], kept: ['_probe_1'] }
  })
  await api.runPurgeAll()
  const get = (n) => calls.filter((c) => c[0] === n).map((c) => c[1])
  out.ok = {
    called,
    busy: get('setBusy'),
    tone: get('setTone'),
    status: get('say').filter(Boolean).join(' | '),
    report: get('setReport'),
    draft: get('setDraft'),
    drafts: get('setDrafts'),
    installed: get('setInstalled'),
    plan: get('setChapterPlan'),
    preview: get('setPreview'),
    name: get('setName'),
    text: get('setText'),
    llmSid: get('setLlmSid'),
    onlyB: get('onlyB'),
    forbidden: { ...forbidden }
  }
}

/* Прогон 2: ядро отказало — честный текст, поля не трогаем. */
{
  calls.length = 0
  forbidden.src = 0; forbidden.cat = 0
  const api = build(async () => ({ ok: false, error: 'staging занят' }))
  await api.runPurgeAll()
  out.failed = {
    tone: calls.filter((c) => c[0] === 'setTone').map((c) => c[1]),
    status: calls.filter((c) => c[0] === 'say').map((c) => c[1]).join(' | '),
    reportTouched: calls.filter((c) => c[0] === 'setReport').length,
    busy: calls.filter((c) => c[0] === 'setBusy').map((c) => c[1])
  }
}

/* Прогон 3: сеть упала — исключение не должно всплывать наружу. */
{
  calls.length = 0
  const api = build(async () => { throw new Error('connection refused') })
  let threw = false
  try { await api.runPurgeAll() } catch (e) { threw = true }
  out.netErr = { threw, tone: calls.filter((c) => c[0] === 'setTone').map((c) => c[1]) }
}

console.log(JSON.stringify(out))
"""


def main() -> int:
    src = PLUGIN.read_text(encoding="utf-8", errors="replace")
    routes = API_ROUTES.read_text(encoding="utf-8", errors="replace")
    core = CORE.read_text(encoding="utf-8", errors="replace")

    # 1. функции живые, а не пересказ
    try:
        fn_purge = cut_function(src, "const runPurgeAll = ")
        fn_reset = cut_function(src, "const resetAfterPurge = ")
        src_slice = fn_purge + "\n" + fn_reset
        check("runPurgeAll и resetAfterPurge вырезаны из plugin.js", True,
              note=f"{len(fn_purge)} и {len(fn_reset)} символов")
    except ValueError as exc:
        check("runPurgeAll и resetAfterPurge вырезаны из plugin.js", False, str(exc))
        return report()

    # Стенд перечисляет сеттеры вручную, и новая пара состояния обгоняет его молча: срез
    # зовёт setNameSeen, фабрика такого параметра не знает - node падает с ReferenceError,
    # и тест валит ЖИВУЮ панель вместо стенда (ловилось на правке «зелёная рамка шага 3»).
    # Поэтому сверяем: каждый сеттер из среза обязан быть параметром стенда.
    used_setters = set(re.findall(r"(?<![\w.$])(set[A-Z][\w$]*)\s*\(", src_slice))
    listed_setters = set(re.findall(r"'(set[A-Z][\w$]*)'", RUNNER))
    missing_setters = sorted(used_setters - listed_setters)
    check("стенд знает все сеттеры, которые зовут runPurgeAll и resetAfterPurge",
          not missing_setters,
          "срез зовёт незаданные стенду сеттеры: " + ", ".join(missing_setters) +
          " - node упадёт с ReferenceError, и живая панель будет выглядеть сломанной")

    # прогон в node
    with tempfile.TemporaryDirectory() as tmp:
        runner = Path(tmp) / "purge.mjs"
        runner.write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ, PURGE_SRC=os.path.join(tmp, "slice.js"))
        Path(env["PURGE_SRC"]).write_text(src_slice, encoding="utf-8")
        proc = subprocess.run(["node", str(runner)], capture_output=True, text=True,
                              env=env, timeout=60)
    if proc.returncode != 0:
        check("панель исполнилась в node", False, (proc.stderr or "").strip()[:300])
        return report()
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    ok = out["ok"]
    # 2. зовём ядро, а не чистим сами
    check("панель зовёт ядро на /purge", ok["called"] == {"path": "/purge", "method": "POST"},
          str(ok["called"]))
    # 3. сброс полей
    check("после уборки сброшены поля черновика и разбора",
          ok["report"] == [None] and ok["draft"] == [None] and ok["drafts"] == [None]
          and ok["installed"] == [None] and ok["plan"] == [None] and ok["preview"] == [None],
          f"report={ok['report']} draft={ok['draft']} installed={ok['installed']}")
    check("сброшены имя, текст источника и состояние LLM",
          ok["name"] == [""] and ok["text"] == [""] and ok["llmSid"] == [""],
          f"name={ok['name']} text={ok['text']} llmSid={ok['llmSid']}")
    check("панель вернулась к блоку 1", ok["onlyB"] == [1], str(ok["onlyB"]))
    # 4. источник и категория не тронуты
    check("источник и категория НЕ сброшены (решение владельца)",
          ok["forbidden"] == {"src": 0, "cat": 0}, str(ok["forbidden"]))
    # 5. статус говорит, что убрано
    check("статус называет убранное (каталоги, сырьё, служебные)",
          "мусор убран" in ok["status"] and "2" in ok["status"] and "_probe_1" in ok["status"],
          ok["status"])
    # 6. отказ и сеть
    check("отказ ядра не молчит: текст ошибки в панели",
          "очистка не прошла" in out["failed"]["status"] and "staging занят" in out["failed"]["status"]
          and out["failed"]["reportTouched"] == 0,
          out["failed"]["status"] + f" | setReport={out['failed']['reportTouched']}")
    check("сеть упала - панель не бросает исключение наружу",
          out["netErr"]["threw"] is False and "error" in out["netErr"]["tone"],
          str(out["netErr"]))
    check("кнопка разблокируется в любом случае (setBusy в конце пуст)",
          ok["busy"][-1] == "" and out["failed"]["busy"][-1] == "", str(ok["busy"]))

    # 7. плашка LLM: без подписи задачи
    head_start = src.index("/* Работа LLM - ПЕРВОЙ веткой")
    head_slice = src[head_start:head_start + 900]
    check("плашка работы LLM читается просто «работает LLM...»",
          "cutSpan('работает LLM...')" in head_slice and "llmLabel" not in head_slice,
          head_slice[:160].replace("\n", " "))
    # 8. кнопка-урна
    check("в шапке есть кнопка-урна, зовущая runPurgeAll",
          "children: cutSpan('🗑️', GLYPH_CLS, TIP.purgeAll)" in src
          and "onClick: runPurgeAll" in src)
    check("урна того же размера, что кнопки блока 1 (icon-xs, 24×24)",
          "size: 'icon-xs'" in src[max(0, src.find("onClick: runPurgeAll") - 400)
                                    :src.find("onClick: runPurgeAll") + 400],
          "владелец просил ширину как у «Выбрать файл» / «Вставить из буфера обмена»")
    check("подсказка урны едет подписью, а не title кнопки (SDK title не отдаёт)",
          "title: TIP.purgeAll" not in src)
    # 9. подсказка владельца дословно
    check("подсказка урны - дословно просьба владельца",
          "purgeAll: 'Очистить промежуточные результаты работы и черновики (убрать мусор)'" in src)
    # 10. ядро и CLI
    check("маршрут /purge объявлен в ядре плагина и зовёт do_purge_all",
          '@router.post("/purge")' in routes and "do_purge_all" in routes)
    check("у ядра есть CLI-команда purge",
          'sub.add_parser("purge"' in core and 'args.cmd == "purge"' in core)
    check("ядро защищает служебные каталоги (_probe*) от уборки",
          'if d.name.startswith("_")' in core and "kept.append" in core)

    # 11. ядро: настоящая уборка на временном каталоге (probe и профиль не трогаются)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("b2s_core", CORE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            fetched = root / "b2s_fetched"
            staging.mkdir()
            fetched.mkdir()
            (staging / "draft-a" / "chapters").mkdir(parents=True)
            (staging / "draft-a" / "chapters" / "ch01.md").write_text("x", encoding="utf-8")
            (staging / "draft-a" / "SKILL.md").write_text("x", encoding="utf-8")
            (staging / "_probe_j").mkdir()
            (staging / "_probe_j" / "f.md").write_text("x", encoding="utf-8")
            (staging / "merge-plan.json").write_text("{}", encoding="utf-8")
            (fetched / "src.md").write_text("x", encoding="utf-8")
            (fetched / "src.report.json").write_text("{}", encoding="utf-8")
            mod.STAGING = staging
            mod.FETCH_DIR = fetched
            res = mod.do_purge_all()
            check("ядро убрало каталог черновика и сырьё, а служебное оставило",
                  res["ok"] and res["dirs"] == ["draft-a"]
                  and sorted(res["source_files"]) == ["merge-plan.json", "src.md", "src.report.json"]
                  and res["kept"] == ["_probe_j"]
                  and not (staging / "draft-a").exists()
                  and (staging / "_probe_j" / "f.md").is_file()
                  and not (fetched / "src.md").exists(),
                  json.dumps(res, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        check("ядро убрало каталог черновика и сырьё, а служебное оставило", False,
              f"{type(exc).__name__}: {exc}")

    return report()


def report() -> int:
    bad = [n for n, ok in checks if not ok]
    print(f"\nпроверок: {len(checks)}, провалов: {len(bad)}")
    if bad:
        print("провалились:")
        for n in bad:
            print("  -", n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
