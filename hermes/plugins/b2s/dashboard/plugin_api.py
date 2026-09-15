"""BOOK → SKILL — REST-маршруты book-to-skill в живом Hermes dashboard.

Зачем этот файл
---------------
Детерминированная часть панели (загрузка источника, очистка текста, отчёт,
перенос готового черновика в ``skills/<категория>/<имя>/``) не требует LLM, а
значит не должна уезжать «событием» в чат. Ядро Hermes монтирует этот файл под
``/api/plugins/b2s/`` (``hermes_cli/web_server_dashboard.py``:
``_mount_plugin_api_routes`` — нужен ``api`` в ``dashboard/manifest.json`` и имя
плагина в ``plugins.enabled``), а панель дёргает маршруты через
``ctx.rest('/api/plugins/b2s/rerun', …)``.

LLM остаётся только там, где без него нельзя: написать главы черновика и
разобрать их — это по-прежнему уходит в чат (``prompt.submit``).

Маршруты (все — ``/api/plugins/b2s`` + путь ниже)
-------------------------------------------------
    GET  /health              живо ли ядро, где форк, какие черновики
    GET  /state               состояние панели + история прогонов
    GET  /categories          существующие категории скиллов (список для выпадашки)
    GET  /skills              существующие скиллы профиля: имя = тема, главы внутри
    GET  /draft?name=…        сводка черновика в staging: файлы, главы, объём, шапка
    POST /mark_ready {…}      маркер готовности черновика: его кладёт LLM последним
                              шагом; без маркера запись в профиль ядро не пустит
    POST /draft_text {…}      текст файла черновика (по клику внутри блока черновика)
    POST /rerun   {src,…}     каскад: загрузка + очистка + отчёт
    POST /resolve {src}       ключ черновика (слаг источника) + предложенное имя — до разбора
    POST /text    {limit,…}   очищенный текст источника — то, что видно в панели
    POST /install {name,…}    план (added/overwrite/keep); пишет ТОЛЬКО при confirm=true,
                              режим: auto | create | append (долив) | replace (с бэкапом)
    POST /plan    {name,…}    долив по главам: что слить со старыми, что переписать,
                              что добавить; плюс готовая постановка для LLM. Не пишет.
    GET  /drafts              рабочие каталоги staging: объём, источник, установлен ли,
                              и вердикт уборки по каждому
    POST /drop    {key|src}   убрать ОДИН рабочий каталог (и его сырьё в b2s_fetched)
    POST /prune   {keep,days} убрать лишние: установленные по TTL, свежие - сверх лимита;
                              без apply это только план

Зависимостей нет: ядро — обычный Python форка (``tools/api.py``).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

PLUGIN_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PLUGIN_DIR / "config.json"
# $HERMES_HOME/plugins/b2s/dashboard → parents[2] == профиль Hermes: он же дом
# venv-интерпретатора, которым гоняются гейты.
HERMES_HOME = Path(os.environ.get("HERMES_HOME") or PLUGIN_DIR.parents[2])

_lock = threading.Lock()
_core: Any = None


def _read_config() -> Dict[str, Any]:
    """Локальный config.json плагина (его пишет установщик) — или пусто."""
    try:
        loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def _guess_python() -> str:
    """Интерпретатор с зависимостями форка: venv Hermes рядом с профилем.

    В службе ``sys.executable`` — это hermes.exe, а не python, поэтому сначала
    пробуем venv профиля и только потом откатываемся на текущий процесс.
    """
    candidates = [
        HERMES_HOME / "hermes-agent" / "venv" / "Scripts" / "python.exe",  # Windows
        HERMES_HOME / "hermes-agent" / "venv" / "bin" / "python",          # POSIX
        Path(sys.executable),
    ]
    for cand in candidates:
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            continue
    return sys.executable


def config() -> Dict[str, str]:
    """Настройки плагина: путь форка и интерпретатор для гейтов.

    Путь к форку НЕ зашит в код: он приходит из окружения (``B2S_FORK``) либо из
    ``config.json``, который пишет ``tools/install_plugin.py`` при установке.
    Зашитый путь сделал бы плагин непереносимым на другую машину.
    """
    data = _read_config()
    fork = os.environ.get("B2S_FORK") or data.get("fork") or ""
    python = os.environ.get("B2S_PYTHON") or data.get("python") or _guess_python()
    return {"fork": str(fork), "python": str(python)}


def core() -> Any:
    """Загрузить ``tools/api.py`` форка — один раз на процесс dashboard."""
    global _core
    with _lock:
        if _core is not None:
            return _core
        cfg = config()
        if not cfg["fork"]:
            raise HTTPException(status_code=503, detail=(
                "не задан путь к клону book-to-skill: запусти tools/install_plugin.py "
                "в клоне (он создаст config.json рядом с plugin_api.py) или задай "
                "переменную окружения B2S_FORK"))
        api_file = Path(cfg["fork"]) / "tools" / "api.py"
        if not api_file.is_file():
            raise HTTPException(status_code=503,
                                detail=f"ядро book-to-skill не найдено: {api_file}")
        os.environ.setdefault("B2S_PYTHON", cfg["python"])
        tools_dir = str(api_file.parent)
        for path in (str(api_file.parent.parent), tools_dir):
            if path not in sys.path:
                sys.path.insert(0, path)
        spec = importlib.util.spec_from_file_location("b2s_tools_api", api_file)
        if spec is None or spec.loader is None:
            raise HTTPException(status_code=503, detail="не удалось загрузить ядро")
        module = importlib.util.module_from_spec(spec)
        sys.modules["b2s_tools_api"] = module   # аннотации pydantic резолвятся по имени
        spec.loader.exec_module(module)
        _core = module
        return module


router = APIRouter()


class RerunBody(BaseModel):
    src: str = ""
    strat: str = ""
    mode: str = ""
    name: str = ""
    cat: str = ""
    depth: str = ""
    lang: str = ""


class ResolveBody(BaseModel):
    """Строка источника: панель спрашивает ключ черновика и имя ЕЩЁ ДО разбора."""
    src: str = ""


class InstallBody(BaseModel):
    name: str = ""
    cat: str = ""
    confirm: bool = False
    force: bool = False
    mode: str = "auto"            # auto | create | append | replace
    allow_overwrite: bool = True  # долив: можно ли трогать существующие файлы скилла
    cat_desc: str = ""            # описание новой категории → DESCRIPTION.md
    src: str = ""                 # источник: по нему ищется черновик (слаг), имя - запасной ключ


class PlanBody(BaseModel):
    """План долива по главам: раскладка сходства + постановка для LLM."""
    name: str = ""
    cat: str = ""
    mode: str = "auto"           # auto | create | append | replace
    threshold: float = 0.35      # с какой близости предлагать слияние
    save: bool = False           # положить план в staging/<слаг источника>/merge-plan.json
    src: str = ""                # источник: по нему ищется черновик (слаг)


class DescBody(BaseModel):
    """Описание категории: текст, который Hermes показывает агенту про категорию."""
    cat: str = ""
    text: str = ""
    mode: str = "write"   # write | fix (обернуть в frontmatter прозу без шапки)
    force: bool = False   # перезаписать существующее описание


class TextBody(BaseModel):
    """Показ очищенного текста в панели: пустой path = последний прогон."""
    path: str = ""
    offset: int = 0
    limit: int = 6000


class DraftTextBody(BaseModel):
    """Текст файла черновика: пустой file = SKILL.md, limit 0 = весь файл."""
    name: str = ""
    file: str = ""
    offset: int = 0
    limit: int = 0
    src: str = ""    # источник: по нему находится черновик (слаг); имя - запасной ключ

class DraftBody(BaseModel):
    """Сводка черновика: пустое имя = самый свежий черновик в staging."""
    name: str = ""
    src: str = ""    # источник черновика: имя скилла правится свободно, источник - нет


class DraftReadyBody(BaseModel):
    """Маркер готовности черновика: кто его ставит и с какими цифрами.

    Пишет его LLM (агент) последним шагом; панель ходит сюда кнопкой «готово», а
    ``ready=false`` снимает маркер, когда агент вернулся к правкам.
    """
    name: str = ""
    src: str = ""
    ready: bool = True
    note: str = ""
    by: str = ""
    files: int = 0
    chapters: int = 0
    terms: int = 0


class DraftsBody(BaseModel):
    """Список рабочих каталогов: панель ходит телом, как в /draft и /plan."""
    src: str = ""    # активный источник: его черновик из очереди уборки исключается


class DropBody(BaseModel):
    """Уборка ОДНОГО рабочего каталога: каталог черновика + его сырьё."""
    key: str = ""            # имя каталога в staging
    src: str = ""            # либо источник: каталог найдётся по слагу
    with_source: bool = True # убрать вместе с сырьём в b2s_fetched


class PruneBody(BaseModel):
    """Уборка лишнего: установленные по TTL, неустановленные - сверх лимита."""
    keep: int = 0            # 0 = взять дефолт ядра (STAGING_KEEP)
    days: int = 0            # 0 = взять дефолт ядра (STAGING_TTL_DAYS)
    src: str = ""            # активный источник: его черновик не трогаем
    apply: bool = False      # false = только план, диск не трогаем


@router.get("/health")
def health() -> Dict[str, Any]:
    """Быстрая проба: панель показывает «ядро: на связи» без прогонов."""
    module = core()
    out = module.do_health()
    out["fork"] = config()["fork"]
    return out


@router.get("/state")
def state() -> Dict[str, Any]:
    return core().do_state()


@router.get("/categories")
def categories() -> Dict[str, Any]:
    """Категории профиля + состояние их DESCRIPTION.md.

    Список — рекомендация, а не забор: в панели есть «своя категория», но панель
    предупреждает, что выдуманное имя уводит скилл мимо агента (категория — это
    механизм подбора), а у новой категории нет описания. Состояние описания
    (``ok`` / ``no-frontmatter`` / ``no-file``) панель показывает чипсой.
    """
    return core().do_categories()


@router.get("/skills")
def skills() -> Dict[str, Any]:
    """Скиллы, которые уже стоят в профиле: имя, категория, число глав, объём.

    Нужен панели, чтобы имя выбирали из списка, а не придумывали. Занятое имя —
    это не запрет, а сигнал «долив в существующий скилл»: панель сама
    переключится в режим дополнения и покажет, что именно изменится.
    """
    return core().do_skills()


@router.post("/rerun")
def rerun(body: RerunBody) -> Dict[str, Any]:
    """Шаг 1 панели: загрузить источник и очистить текст. Без LLM."""
    return core().do_rerun(body.src, body.strat, body.mode, body.name,
                           body.cat, body.depth, body.lang)


@router.post("/resolve")
def resolve(body: ResolveBody) -> Dict[str, Any]:
    """Ключ черновика и предложенное имя по строке источника — без сети и LLM.

    Панель зовёт это при вводе источника, до всякого разбора: ключ черновика
    следует за ПОЛЕМ, а не за прошлым прогоном ядра. Иначе после смены url в блоке
    «Черновик скилла» оставались файлы прежней работы, и панель говорила о чужом
    черновике как о своём.
    """
    return core().do_resolve(body.src)


@router.post("/text")
def text(body: TextBody) -> Dict[str, Any]:
    """Очищенный текст источника: панель показывает его тем же экраном, что и метрики."""
    return core().do_text(body.path, body.offset, body.limit)


@router.get("/draft")
def draft(name: str = "", src: str = "") -> Dict[str, Any]:
    """Сводка черновика в staging — заголовок блока «Черновик скилла».

    Блок стоит свёрнутым, поэтому заголовок обязан быть ФАКТОМ: файлов, глав,
    символов, время. Пустой staging — не ошибка (``has_draft: false``): панель
    скажет «черновика ещё нет — шаг 2», а не нарисует аварию.

    Отдельным маршрутом, а не только внутри ``/state``, потому что черновик
    пишет LLM в чате: панель обновляет сводку по кнопке и при раскрытии блока,
    не перезапуская всю панель.
    """
    return core().do_draft(name, src)


@router.post("/draft")
def draft_post(body: DraftBody) -> Dict[str, Any]:
    """То же, что ``GET /draft``, но телом: панель ходит POST'ом, как в /text и /plan.

    Одна и та же сводка на двух глаголах — не дубль: GET удобен для curl и проб,
    POST — для панели, которой иначе пришлось бы клеить query-строку к пути
    маршрута (в ``ctx.rest`` это лишний риск, а поведение одинаково).
    """
    return core().do_draft(body.name, body.src)


@router.post("/mark_ready")
def mark_ready(body: DraftReadyBody) -> Dict[str, Any]:
    """Поставить или снять маркер готовности черновика.

    Основной автор - LLM: агент дописал главы, прогнал проверки и помечает черновик
    готовым. Маршрут нужен панели (кнопка «готово») и человеку за curl; пока маркера
    нет, ядро отказывает в записи в профиль - иначе туда уехал бы обрывок.
    """
    return core().do_mark_ready(body.name, body.src, body.ready, body.note,
                                body.by, body.files, body.chapters, body.terms)


@router.post("/draft_text")
def draft_text(body: DraftTextBody) -> Dict[str, Any]:
    """Текст файла черновика — то, что панель показывает по клику внутри блока.

    Ядро режет путь по каталогу черновика (``..`` и абсолютные пути отклоняются)
    и умеет отдавать окно ``offset…offset+limit``: панель берёт первый экран,
    а остаток догружает, а не тянет весь скилл на каждый рендер.
    """
    return core().do_draft_text(body.name, body.file, body.offset, body.limit, body.src)


@router.get("/drafts")
def drafts(src: str = "") -> Dict[str, Any]:
    """Рабочие каталоги staging: источник, объём, установлен ли, что подлежит уборке.

    Панель зовёт это после разбора и по кнопке: человек должен ВИДЕТЬ, что рядом
    лежит черновик другого источника и что именно уйдёт при уборке, а не узнавать
    об этом по исчезнувшим файлам.
    """
    return core().do_drafts(src)


@router.post("/drafts")
def drafts_post(body: DraftsBody) -> Dict[str, Any]:
    """То же, что ``GET /drafts``, но телом: панель не клеит query к пути маршрута."""
    return core().do_drafts(body.src)


@router.post("/drop")
def drop(body: DropBody) -> Dict[str, Any]:
    """Убрать ОДИН рабочий каталог: черновик в staging и его сырьё в b2s_fetched.

    Скилл в профиле не трогается: staging - мастерская, а не витрина. Служебные
    каталоги (`_probe*`) ядро убирать откажется: на них стоят тесты ядра.
    """
    return core().do_drop_draft(body.key, body.src, body.with_source)


@router.post("/prune")
def prune(body: PruneBody) -> Dict[str, Any]:
    """Убрать лишние каталоги: установленные по TTL, свежие - сверх лимита.

    По умолчанию это ПЛАН (``apply=false``): панель сначала показывает список и
    только по подтверждению удаляет. Так автоуборка не может стереть черновик,
    над которым человек ещё работает.
    """
    module = core()
    keep = body.keep or module.STAGING_KEEP
    days = body.days or module.STAGING_TTL_DAYS
    return module.do_prune_staging(keep, days, body.src, body.apply)


@router.post("/plan")
def plan(body: PlanBody) -> Dict[str, Any]:
    """Раскладка по главам для долива: слить / переписать / добавить.

    Решение принимает LLM (в панели — кнопкой «отправить агенту»), а ядро даёт
    детерминированную часть: для каждой главы черновика — ближайшие по теме
    файлы скилла и близость. Запись в скилл тут невозможна по построению.
    """
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="нужно имя скилла")
    return core().do_chapter_plan(body.name, body.cat, body.mode, body.save, body.threshold,
                                  body.src)


@router.post("/desc")
def desc(body: DescBody) -> Dict[str, Any]:
    """Записать DESCRIPTION.md категории — пояснение, которое читает Hermes.

    Файл идёт в промпт (``agent/prompt_builder.py:_read_category_descriptions``),
    поэтому проза без frontmatter для агента невидима: режим ``fix`` оборачивает
    её в шапку, сохраняя текст телом. Уже существующее описание без ``force`` не
    трогаем — чужой текст не затираем молча.
    """
    if not body.cat.strip():
        raise HTTPException(status_code=400, detail="нужна категория")
    return core().do_write_category_desc(body.cat, body.text, body.mode, body.force)


@router.post("/install")
def install(body: InstallBody) -> Dict[str, Any]:
    """Шаг 3 панели: план переноса. Запись — только при confirm=true.

    Без confirm возвращается план: что добавится, что перезапишется, что
    останется от прежнего скилла. Панель показывает его перед подтверждением —
    «замещение» вслепую здесь недопустимо.
    """
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="нужно имя скилла")
    return core().do_install(body.name, body.cat, body.confirm, body.force,
                             body.mode, body.allow_overwrite, body.cat_desc, body.src)
