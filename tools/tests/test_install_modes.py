"""Живой тест режимов установки: план → create → долив → замена, с бэкапом.

Песочница: HERMES_HOME=D:/tmp/b2s-fakehome, черновик-копия staging/_probe.
Профиль не трогаем — проверяем ядро (tools/api.py do_install/do_skills).
"""
import json
import os
import shutil
import sys
from pathlib import Path

FAKE = Path("D:/tmp/b2s-fakehome")
REAL_HOME = os.environ.get("HERMES_HOME", "")   # настоящий профиль вернём после проверки
os.environ["HERMES_HOME"] = str(FAKE)
sys.path.insert(0, "D:/.VS/Projects/BOOK-TO-SKILL/_fork/tools")
import api  # noqa: E402

REPO = api.REPO
probe = REPO / "staging" / "_probe"
shutil.rmtree(FAKE, ignore_errors=True)
shutil.rmtree(REPO / "backups", ignore_errors=True)
shutil.rmtree(probe, ignore_errors=True)
shutil.copytree(REPO / "staging" / "python-pathlib", probe)

target = FAKE / "skills" / "software-development" / "_probe"


def show(tag, out):
    print(f"\n--- {tag}")
    print(f"    mode={out.get('mode')} counts={out.get('plan_counts')} risk={out.get('risk')} "
          f"ok={out.get('ok')} dry={out.get('dry_run')}")
    if out.get("warning"):
        print(f"    warn: {out['warning']}")
    if out.get("error"):
        print(f"    ERROR: {out['error']}")
    for key in ("added", "overwrite", "keep", "same"):
        vals = (out.get("plan") or {}).get(key) or []
        if vals:
            shown = ", ".join(vals[:8])
            print(f"    {key}({len(vals)}): {shown}{' …' if len(vals) > 8 else ''}")
    if out.get("backup"):
        print(f"    backup: {out['backup']}")
    if out.get("wrote") is not None and not out.get("dry_run"):
        print(f"    wrote({len(out.get('wrote') or [])})")
    val = out.get("validation") or {}
    if val:
        print(f"    validate.ok={val.get('validate', {}).get('ok')} scan.ok={val.get('scan', {}).get('ok')}")
    return out


print("=== 0. список скиллов профиля (реальный, не песочница) ===")
os.environ["HERMES_HOME"] = REAL_HOME
sk = api.do_skills()
print(f"count={sk['count']}  root={sk['root']}")
ch = [s for s in sk["skills"] if s["chapters"]]
print(f"со главами: {len(ch)}  примеры: "
      + ", ".join(f"{s['name']}({s['chapters']}гл/{s['category'] or '—'})" for s in ch[:5]))
for want in ("apk-to-maui", "python-pathlib"):
    hit = [s for s in sk["skills"] if s["name"] == want]
    print(f"    {want}: " + (f"категория={hit[0]['category']!r} файлов={hit[0]['files']} "
                             f"глав={hit[0]['chapters']}" if hit else "НЕТ в профиле"))
os.environ["HERMES_HOME"] = str(FAKE)

print("\n=== 1. план без записи (цели нет → create) ===")
show("план", api.do_install("_probe", "software-development"))

print("\n=== 2. установка create ===")
show("create", api.do_install("_probe", "software-development", confirm=True))
print(f"    на диске: {sorted(p.name for p in target.iterdir())}")
print(f"    глав: {len(list((target / 'chapters').glob('*.md')))}")

print("\n=== 3. готовим долив: новая глава ch99 + правка SKILL.md + пропала глава ch09 ===")
(probe / "chapters" / "ch99-probe.md").write_text("# ch99 probe\n\nдолив\n", encoding="utf-8")
with (probe / "SKILL.md").open("a", encoding="utf-8") as fh:
    fh.write("\n<!-- probe edit -->\n")
gone = sorted((probe / "chapters").glob("ch09*"))
if gone:
    gone[0].unlink()
    print(f"    из черновика убрана {gone[0].name} (в скилле осталась → ожидаем keep)")
print(f"    в скилле глав до долива: {len(list((target / 'chapters').glob('*.md')))}")

print("\n=== 4. план долива (append, без записи) ===")
show("план долива", api.do_install("_probe", "software-development", mode="append"))

print("\n=== 5. долив с перезаписью разрешённой (append) ===")
out = show("append", api.do_install("_probe", "software-development", confirm=True, mode="append"))
print(f"    на диске главы: {len(list((target / 'chapters').glob('*.md')))} "
      f"(+ch99 = {len(list((probe / 'chapters').glob('*.md'))) + 1})")
print(f"    ch99 в скилле: {(target / 'chapters' / 'ch99-probe.md').is_file()}")
print(f"    правка SKILL.md доехала: {'<!-- probe edit -->' in (target / 'SKILL.md').read_text(encoding='utf-8')}")
print(f"    удалённая глава осталась (keep): {gone[0].name if gone else '—'} → "
      f"{(target / 'chapters' / gone[0].name).is_file() if gone else 'n/a'}")
print(f"    бэкапов: {len(list((REPO / 'backups').glob('_probe-*')))}")

print("\n=== 6. долив строго «только новое» (--no-overwrite) ===")
(probe / "SKILL.md").write_text("# _probe\n\nновая версия индекса\n", encoding="utf-8")
out = show("append --no-overwrite",
           api.do_install("_probe", "software-development", confirm=True,
                          mode="append", allow_overwrite=False))
print(f"    SKILL.md остался прежним: {'новая версия' not in (target / 'SKILL.md').read_text(encoding='utf-8')}")

print("\n=== 7. замена целиком (replace) с бэкапом ===")
show("replace", api.do_install("_probe", "software-development", confirm=True, mode="replace"))
print(f"    в скилле глав: {len(list((target / 'chapters').glob('*.md')))} "
      f"(= в черновике {len(list((probe / 'chapters').glob('*.md')))})")
print(f"    старая глава снесена: {not (target / 'chapters' / gone[0].name).is_file() if gone else 'n/a'}")
print(f"    бэкапов: {len(list((REPO / 'backups').glob('_probe-*')))} → "
      f"{[p.name for p in sorted((REPO / 'backups').glob('_probe-*'))]}")
latest = sorted((REPO / "backups").glob("_probe-*"))
if latest:
    print(f"    в последнем бэкапе глава есть: "
          f"{(latest[-1] / 'chapters' / gone[0].name).is_file() if gone else 'n/a'}")

print("\n=== 8. auto на существующей цели — должен выбрать append, не create ===")
show("auto", api.do_install("_probe", "software-development"))

print("\n=== 9. чистка песочницы ===")
shutil.rmtree(probe, ignore_errors=True)
shutil.rmtree(FAKE, ignore_errors=True)
shutil.rmtree(REPO / "backups", ignore_errors=True)
print("убрано:", probe, FAKE, REPO / "backups")
