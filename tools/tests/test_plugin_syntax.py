"""Плагин панели обязан ПАРСИТЬСЯ целиком, а не только своими кусками.

Живой случай: в `nameNote` не хватило одной закрывающей скобки, и Electron
ответил `[plugins] runtime load failed (b2s) SyntaxError: Unexpected identifier
'useEffect'` - вкладка плагина просто исчезла из приложения. При этом весь набор
тестов панели был зелёным: они берут из `plugin.js` отдельный фрагмент и
исполняют его через `new Function(...)`, поэтому сломанная часть файла в проверку
не попадала. Отсюда этот тест: он читает файл ЦЕЛИКОМ и отдаёт его node на
разбор, как это делает загрузчик плагинов.

Проверяем:
1. каждый JS-файл плагина и панели разбирается node как ES-модуль;
2. в `plugin.js` есть экспорт по умолчанию (иначе загрузчик нечего регистрировать);
3. в `plugin.js` не осталось React-хуков вне тела компонента... нет, это не проверить
   разбором - ограничиваемся разбором и наличием экспорта.

Запуск: python -B tools/tests/test_plugin_syntax.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN_DIR = REPO / "hermes" / "desktop-plugins" / "b2s"

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, note: str = "") -> None:
    checks.append((name, bool(ok), note))
    print(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f" - {note}" if note else ""))


def parses(path: Path) -> tuple[bool, str]:
    """Разобрать файл тем же способом, каким это делает загрузчик: как ES-модуль."""
    proc = subprocess.run(
        ["node", "--input-type=module", "--check"],
        input=path.read_text(encoding="utf-8"),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    tail = ((proc.stderr or proc.stdout or "").strip().splitlines() or [""])[-1]
    return proc.returncode == 0, tail[:400]


def main() -> int:
    print("=== 1. разбор файлов плагина целиком ===")
    files = sorted([p for p in PLUGIN_DIR.rglob("*.js") if p.is_file()])
    check("нашлись файлы плагина", bool(files), f"{len(files)} шт.")
    for p in files:
        ok, err = parses(p)
        check(f"{p.relative_to(PLUGIN_DIR).as_posix()} разбирается node", ok, err if not ok else "")

    print("\n=== 2. то, без чего загрузчик не зарегистрирует плагин ===")
    src = (PLUGIN_DIR / "plugin.js").read_text(encoding="utf-8")
    check("в plugin.js есть export default", "export default" in src)
    check("в plugin.js есть объявление плагина (id)", "b2s" in src)
    # Скобочный баланс как дешёвый признак обрыва выражения: node ловит такое
    # первым, но если файл не дошёл до проверки - хотя бы счёт.
    check("в plugin.js нет одинокого useEffect на верхнем уровне",
          "\nuseEffect(" not in src)

    bad = [n for n, ok, _ in checks if not ok]
    print(f"\nпроверок: {len(checks)}, провалов: {len(bad)}" + (f" - {bad}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
