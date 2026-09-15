#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Граф перекрёстных ссылок между скиллами ОДНОЙ категории.

Читает все ``.md`` каждого скилла каталога, ищет имена соседних скиллов ЭТОЙ ЖЕ
категории в бэктиках и строит направленный граф. Умеет обновлять ``related_skills``
в шапках - и только между скиллами этой категории.

Запуск:
    python links.py --skills-dir <каталог скиллов> [--json] [--apply]

``--apply`` переписывает ``related_skills`` в ``SKILL.md`` каждого скилла по факту
графа; без него это только чтение.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys


def names_of(root: pathlib.Path) -> set[str]:
    """Имена скиллов категории: только каталоги (файлы вроде merge-plan.json - нет)."""
    return {p.name for p in root.iterdir() if p.is_dir()}

def collect(names: set[str], root: pathlib.Path) -> set[tuple[str, str]]:
    """Рёбра (источник, цель): имя соседнего скилла категории в бэктиках."""
    edges: set[tuple[str, str]] = set()
    for name in sorted(names):
        d = root / name
        if not d.is_dir():
            continue
        text = "\n".join(
            f.read_text(encoding="utf-8", errors="replace")
            for f in sorted(d.rglob("*.md"))
        )
        for other in names:
            if other != name and re.search(r"`" + re.escape(other) + r"`", text):
                edges.add((name, other))
    return edges


def declared(names: set[str], root: pathlib.Path) -> dict[str, set[str]]:
    """Заявленные связи: related_skills в шапке и cross_refs в metadata.json."""
    out: dict[str, set[str]] = {}
    for name in sorted(names):
        d = root / name
        got: set[str] = set()
        skill_md = d / "SKILL.md"
        if skill_md.is_file():
            txt = skill_md.read_text(encoding="utf-8", errors="replace")
            for line in txt.splitlines():
                if "related_skills" in line:
                    got |= {x for x in names if x != name and x in line}
        meta = d / "metadata.json"
        if meta.is_file():
            try:
                data = json.loads(meta.read_text(encoding="utf-8", errors="replace"))
                blob = json.dumps(data.get("cross_refs") or "", ensure_ascii=False)
                got |= {x for x in names if x != name and x in blob}
            except Exception:  # noqa: BLE001
                pass
        out[name] = got
    return out


def apply_related(root: pathlib.Path, edges: set[tuple[str, str]]) -> dict:
    """Записать related_skills по факту графа - ТОЛЬКО внутри этой категории.

    Список у каждого скилла = его исходящие ссылки, по алфавиту. Строка вставляется
    после `tags:` в шапке (или после `metadata.hermes`, если тега нет) и заменяется,
    если уже стоит. Перевод строки берём от файла: скиллы профиля записаны CRLF, и
    новая строка без возврата каретки сделала бы переводы смешанными.
    """
    targets = collections.defaultdict(list)
    for a, b in sorted(edges):
        targets[a].append(b)

    updated, unchanged, skipped = [], [], []
    for d in sorted(x for x in root.iterdir() if x.is_dir()):
        f = d / "SKILL.md"
        if not f.is_file():
            skipped.append(d.name)
            continue
        want = sorted(targets.get(d.name, []))
        if not want:
            skipped.append(d.name)
            continue
        with open(f, encoding="utf-8", newline="") as fh:
            txt = fh.read()
        crlf = "\r\n" in txt
        eol = "\r" if crlf else ""
        lines = txt.split("\n")
        seps = [i for i, l in enumerate(lines) if l.strip() == "---"]
        if len(seps) < 2:
            skipped.append(d.name)
            continue
        start, end = seps[0], seps[1]
        line = "    related_skills: [" + ", ".join(want) + "]" + eol
        have = next((i for i in range(start, end) if "related_skills:" in lines[i]), None)
        if have is not None:
            if lines[have].rstrip("\r") == line.rstrip("\r"):
                unchanged.append(d.name)
                continue
            lines[have] = line
        else:
            anchor = next((i for i in range(start, end) if lines[i].lstrip().startswith("tags:")), None)
            if anchor is None:
                anchor = next((i for i in range(start, end) if "metadata:" in lines[i]), None)
            if anchor is None:
                skipped.append(d.name)
                continue
            lines.insert(anchor + 1, line)
        with open(f, "wb") as fh:
            fh.write("\n".join(lines).encode("utf-8"))
        updated.append(d.name)
    return {"updated": updated, "unchanged": unchanged, "skipped": skipped}


def build_report(root: pathlib.Path) -> dict:
    """Отчёт по категории: рёбра, хабы, сироты, молчуны, расхождения заявленного."""
    names = names_of(root)
    edges = collect(names, root)
    inbound = collections.Counter(b for _, b in edges)
    orphans = sorted(n for n in names if not any(b == n for _, b in edges))
    silent = sorted(n for n in names if not any(a == n for a, _ in edges))
    out = {a: sorted(b for x, b in edges if x == a) for a in sorted(names)}
    decl = declared(names, root)
    gaps = {n: sorted(t for t in decl[n] if (n, t) not in edges) for n in names if decl[n]}
    gaps = {n: v for n, v in gaps.items() if v}
    return {
        "skills_dir": str(root),
        "skills": sorted(names),
        "count": len(names),
        "edges": sorted(edges),
        "edge_count": len(edges),
        "outgoing": out,
        "orphans": orphans,
        "silent": silent,
        "inbound": inbound.most_common(),
        "declared_filled": sorted(n for n in names if decl[n]),
        "declared_gaps": gaps,
    }


def print_report(rep: dict) -> None:
    print(f"каталог: {rep['skills_dir']}")
    print(f"скиллов: {rep['count']} | рёбер: {rep['edge_count']}\n")
    print("кто -> на кого:")
    for a in rep["skills"]:
        print(f"   {a:30s} -> {', '.join(rep['outgoing'][a]) if rep['outgoing'][a] else '-'}")
    print("\nхабы (входящие ссылки):")
    for n, c in rep["inbound"][:8]:
        print(f"   {n:32s} {c}")
    print("\nсироты (на них никто не ссылается):", ", ".join(rep["orphans"]) or "нет")
    print("молчуны (никого не упоминают):", ", ".join(rep["silent"]) or "нет")
    print(f"\nзаявленные связи заполнены у {len(rep['declared_filled'])} из {rep['count']}")
    if rep["declared_gaps"]:
        print("заявлено, но в тексте ссылки нет:")
        for n, v in rep["declared_gaps"].items():
            print(f"   {n:30s} -> {', '.join(v)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="граф перекрёстных ссылок внутри категории скиллов")
    ap.add_argument("--skills-dir", required=True, help="каталог категории (внутри - папки скиллов)")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    ap.add_argument("--apply", action="store_true",
                    help="обновить related_skills по факту графа (только внутри категории)")
    args = ap.parse_args()

    root = pathlib.Path(args.skills_dir)
    if not root.is_dir():
        print(f"нет каталога: {root}", file=sys.stderr)
        return 2
    if not names_of(root):
        print(f"в каталоге нет папок скиллов: {root}", file=sys.stderr)
        return 2

    rep = build_report(root)
    if args.apply:
        rep["apply"] = apply_related(root, {tuple(e) for e in rep["edges"]})

    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print_report(rep)
        if args.apply:
            a = rep["apply"]
            print(f"\nrelated_skills: обновлено {len(a['updated'])}, без изменений {len(a['unchanged'])}, "
                  f"пропущено {len(a['skipped'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
