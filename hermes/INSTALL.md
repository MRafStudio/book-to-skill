# Установка панели BOOK → SKILL на другую машину

Панель состоит из трёх частей, и раньше всех их приходилось раскладывать руками:

1. `desktop-plugins/b2s/plugin.js` — интерфейс в правом rail'е Hermes;
2. `plugins/b2s/{plugin.yaml, __init__.py, dashboard/*}` — backend и REST-мост
   `/api/plugins/b2s/`;
3. `dashboard/config.json` — путь к клону и интерпретатор гейтов (у каждой
   машины свой).

Теперь всё это делает один скрипт из клона: **`tools/install_plugin.py`**.

Ниже — весь путь на чистой машине.

## 0. Что нужно

- Windows (проверялось на Windows 11) и работающий Hermes с профилем
  (`HERMES_HOME` — каталог, где лежат `config.yaml` и `skills/`).
- `git` и Python 3.11+ (подойдёт venv самого Hermes:
  `$HERMES_HOME/hermes-agent/venv/Scripts/python.exe`).
- Для PDF-источников — `pdftotext` (poppler) в `PATH`. Для HTML/Markdown не нужен.

## 1. Клон

```bash
git clone https://github.com/MRafStudio/book-to-skill.git
cd book-to-skill
git checkout agent/url-dashboard        # панель пока живёт в этой ветке
```

## 2. Зависимости интерпретатора

Гейты (`validate_skill.py`, `scan_generated_skill.py`) и извлечение текста
работают тем интерпретатором, который записан в `config.json`. Поставь в него:

```bash
uv pip install --python "$HERMES_HOME/hermes-agent/venv/Scripts/python.exe" \
    trafilatura beautifulsoup4
```

Проверка:

```bash
"$HERMES_HOME/hermes-agent/venv/Scripts/python.exe" -c "import trafilatura, bs4; print('ok')"
pdftotext -v        # необязательно
```

## 3. Установка плагина

```bash
python tools/install_plugin.py --check        # план: что будет записано
python tools/install_plugin.py                # поставить / обновить
```

Полезные ключи:

| Ключ | Зачем |
|---|---|
| `--hermes-home PATH` | если профиль не находится сам (иначе берётся `HERMES_HOME`) |
| `--check` | показать план и ничего не писать |
| `--enable` | сам включить плагин (`hermes plugins enable b2s`) |

Скрипт раскладывает файлы, создаёт `config.json`
(`fork` = путь клона, `python` = интерпретатор) и печатает отчёт по окружению:
есть ли `trafilatura`/`bs4`, `pdftotext` и запись `b2s` в `plugins.enabled`.

## 4. Активация

- **Панель** (`plugin.js`) подхватывается хот-релоадом — перезапускать чат не
  нужно, достаточно переоткрыть правый rail.
- **Backend** монтирует маршруты только на старте → нужен рестарт dashboard
  (перезапуск службы Hermes): `python tools/restart_dashboard.py` (права админа
  скрипт просит сам, имя службы ищет по префиксу `HermesGateway` — на другой
  машине оно другое). `tools/restart_dashboard.bat` рядом — только обёртка.
- **Проверка:**

```bash
python tools/probe_route.py skills     # 200 + контрольный 404 = смонтирован
```

## Если что-то не так

| Симптом | Причина и что делать |
|---|---|
| Панели нет в rail'е | плагин не включён в `plugins.enabled` → `python tools/install_plugin.py --enable` (или `hermes plugins enable b2s`) |
| Панель есть, кнопки отвечают 503 «не задан путь к клону» | нет `config.json` → запусти установщик или задай `B2S_FORK` |
| Маршрут `/api/plugins/b2s/skills` даёт 404 | backend не смонтирован: проверь, что на месте `plugins/b2s/__init__.py` и `plugin.yaml`, и что dashboard рестартован |
| `restart_dashboard.py`: «служба dashboard не найдена» | служба называется иначе — задай явно: `python tools/restart_dashboard.py --name "<имя из services.msc>"` |
| Очищенный текст пустой, «мусор» большой | нет `trafilatura`/`bs4` у интерпретатора из поля `python` в `config.json` |
| Другой клон форка | переопредели: `B2S_FORK=D:/path/to/clone` (или поправь `config.json`) |

## Разработка на этой же машине

Здесь работает обратный инструмент — двусторонний синхронизатор зеркала
`tools/sync_hermes.py` (`--check`, `--pull`, без флага — форк → профиль).
Разница: установщик создаёт `config.json` и проверяет окружение, синхронизатор
— про правки в git и дрейф между зеркалом и профилем.
