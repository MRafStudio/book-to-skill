#!/usr/bin/env python3
"""Кнопка описания категории в блоке 3: три состояния, и ни одно не «пропало».

Зачем: кнопка жила только там, где описания НЕТ (или оно без frontmatter). Как только
файл становился валидным, блок превращался в справку - и переписать устаревшую вывеску
из панели было нельзя (владелец: «есть ощущение, что кнопка не должна исчезать, если
описание категории уже есть - а вдруг надо пересоздать»).

Что проверяем
-------------
1. В состоянии ``ok`` (описание есть) кнопка ЕСТЬ и она «Пересоздать описание категории»;
2. нет описания - «Написать описание категории» (интент ``desc``);
3. проза без frontmatter - «Починить файл» (интент ``desc-fix``);
4. пересоздание уходит отдельным интентом ``desc-rewrite`` с ``dmode=rewrite``;
5. подпись задачи для плашки знает новый вид (``desc-rewrite``);
6. после отправки панель следит за целью и для пересоздания - иначе рамка не обновится;
7. тултип честно предупреждает, что прежний текст будет заменён;
8. ядро принимает режим ``rewrite`` и в CLI, и в самой записи.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "hermes" / "desktop-plugins" / "b2s" / "plugin.js"
CORE = REPO / "tools" / "api.py"

checks: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok))
    print(("  OK   " if ok else "  FAIL ") + name + (f" - {detail}" if not ok and detail else ""))


src = PLUGIN.read_text(encoding="utf-8")
core = CORE.read_text(encoding="utf-8")

# 1-2. ветка «описание есть»: вырезаем кусок от 'catState === 'ok'' до 'no-frontmatter'
i_ok = src.find("children: catState === 'ok'")
i_nf = src.find("catState === 'no-frontmatter'", i_ok)
branch_ok = src[i_ok:i_nf] if 0 < i_ok < i_nf else ""
check("ветка «описание есть» найдена", bool(branch_ok), f"ok@{i_ok} no-frontmatter@{i_nf}")
check("в ветке «описание есть» кнопка НЕ исчезла",
      "chipAction(" in branch_ok and "Пересоздать описание категории" in branch_ok,
      "кнопка снова прячется, когда описание валидно")
check("пересоздание зовёт sendDesc('desc-rewrite')",
      "sendDesc('desc-rewrite')" in branch_ok)
check("ветка пересоздания заперта на время работы",
      "busy === 'desc-rewrite'" in branch_ok)

check("нет описания - кнопка «Написать описание категории»",
      src.count("Написать описание категории") >= 2 and "sendDesc('desc')" in src)
check("проза без шапки - кнопка «Починить файл»",
      "Починить файл" in src and "sendDesc('desc-fix')" in src)

# 4. интент
check("intentOf знает вид desc-rewrite",
      "kind === 'desc-rewrite'" in src and "'rewrite' : 'write'" in src)
check("интент уходит как dmode=rewrite",
      "kind === 'desc-rewrite' ? 'rewrite' : 'write'" in src)

# 5. подпись плашки
check("LLM_LABEL знает пересоздание описания",
      "'desc-rewrite': 'пересоздание описания'" in src)

# 6. слежение за целью
i_watch = src.find("if (kind === 'desc' || kind === 'desc-fix'")
check("вотчер описания поднимается и для пересоздания",
      i_watch > 0 and "desc-rewrite" in src[i_watch:i_watch + 140],
      "без слежения рамка чипсы не обновится")

# 7. тултип
check("тултип говорит, что прежний текст будет заменён",
      "catDescRewrite: 'Задание агенту в чате: пересоздать описание категории - прежний текст будет заменён'" in src)

# 8. ядро
check("ядро принимает режим rewrite",
      'if wanted not in ("write", "fix", "rewrite")' in core)
check("rewrite не требует --force и не тащит старый текст телом",
      'elif wanted == "rewrite":' in core and "Прежний текст НЕ тащим телом" in core)
check("CLI знает choice rewrite",
      'choices=["write", "fix", "rewrite"]' in core)

print(f"\nпроверок: {len(checks)}, провалов: {sum(1 for _, ok in checks if not ok)}")
if any(not ok for _, ok in checks):
    print("провалено: " + "; ".join(name for name, ok in checks if not ok))
    sys.exit(1)
