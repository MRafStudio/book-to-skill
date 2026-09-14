"""Имя скилла, подхваченное из прошлого захода, приводится к нижнему регистру.

Живой случай: в `ctx.storage` осталось имя `license-Info` (прошлый заход), панель
вернула его в поле и подсветила жёлтым «такое имя Hermes не примет» - владелец
увидел ругань на то, чего сам не вводил. Восстановленное имя теперь нормализуется,
набранное руками не трогаем.

Проверяем не текст, а само выражение из живого plugin.js: вырезаем инициализацию
состояния имени и исполняем её в node на нескольких значениях storage.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(("  OK   " if ok else "  FAIL ") + name + (f" — {detail}" if detail else ""))


def cut_init(src: str, decl: str) -> str:
    """Вырезать выражение инициализации: ``const [name, setName] = useState(<expr>)``."""
    start = src.index(decl)
    call = src.index("useState(", start)
    open_paren = src.index("(", call)
    depth = 0
    for i in range(open_paren, len(src)):
        ch = src[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return src[open_paren + 1:i]
    raise ValueError("не найден конец useState: " + decl)


RUNNER = r"""
const expr = process.env.EXPR
const f = new Function('stored', 'return (' + expr + ')')
const out = {
  mixed: f({ name: 'license-Info' }),
  upper: f({ name: 'RKEEPER-LICENSEINFO' }),
  clean: f({ name: 'python-pathlib' }),
  empty: f({}),
  blank: f({ name: '' })
}
console.log(JSON.stringify(out))
"""


def main() -> int:
    if not PLUGIN.is_file():
        print(f"нет файла плагина: {PLUGIN}")
        return 1
    src = PLUGIN.read_text(encoding="utf-8")

    # 1) инициализация состояния имени действительно нормализует восстановленное значение
    try:
        expr = cut_init(src, "const [name, setName] = useState(")
    except ValueError as exc:
        check("инициализация имени найдена в plugin.js", False, str(exc))
        return report()
    check("инициализация имени найдена в plugin.js", True)
    check("нормализация в живом выражении", "toLowerCase" in expr, expr[:120])

    # 2) само выражение считает правильно (exec в node, не копия в тесте)
    with tempfile.TemporaryDirectory() as tmpd:
        f = Path(tmpd) / "run.mjs"
        f.write_text(RUNNER, encoding="utf-8")
        env = dict(os.environ)
        env["EXPR"] = expr
        proc = subprocess.run(
            ["node", str(f)],
            capture_output=True, text=True,
            env=env,
        )
    if proc.returncode != 0:
        check("выражение исполняется в node", False, (proc.stderr or proc.stdout)[-300:])
        return report()
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    check("выражение исполняется в node", True)
    check("license-Info → license-info", out["mixed"] == "license-info", repr(out["mixed"]))
    check("RKEEPER-LICENSEINFO → rkeeper-licenseinfo", out["upper"] == "rkeeper-licenseinfo", repr(out["upper"]))
    check("годное имя не портится", out["clean"] == "python-pathlib", repr(out["clean"]))
    check("пустой storage даёт пустое поле", out["empty"] == "" and out["blank"] == "", repr((out["empty"], out["blank"])))

    # 3) в живом файле нет старой формы без нормализации
    check("старой формы useState(stored.name || '') нет",
          re.search(r"useState\(\s*stored\.name\s*\|\|", src) is None)
    return report()


def report() -> int:
    bad = [n for n, ok in checks if not ok]
    print(f"\nпроверок: {len(checks)}, провалов: {len(bad)}")
    for n in bad:
        print("  провал:", n)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
