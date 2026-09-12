# `hermes/` — зеркало того, что ставится в профиль Hermes

Панель BOOK → SKILL (rail) и её REST-мост живут не в дереве пайплайна, а в
профиле Hermes. Здесь лежит их версионируемая копия, разворачиваемая скриптом
`tools/sync_hermes.py`.

| Файл в зеркале | Куда попадает | Кто подхватывает правку |
|---|---|---|
| `desktop-plugins/b2s/plugin.js` | `$HERMES_HOME/desktop-plugins/b2s/` | хот-релоад панели (сам, без действий) |
| `plugins/b2s/dashboard/plugin_api.py` | `$HERMES_HOME/plugins/b2s/dashboard/` | **только рестарт** dashboard |
| `plugins/b2s/dashboard/manifest.json` | там же | **только рестарт** dashboard |
| `plugins/b2s/dashboard/config.json` | там же | нужен для загрузки ядра; машинно-специфичный |

## Развернуть / проверить

```bash
python tools/sync_hermes.py            # форк → профиль
python tools/sync_hermes.py --check    # показать дрейф, exit 1 если есть
python tools/sync_hermes.py --pull     # профиль → форк (забрать правку в git)
```

Профиль берётся из `--hermes-home`, иначе из `HERMES_HOME`, иначе дефолт
машины (`D:/NEURO/Hermes/data/hermes`).

**Что требуется в профиле, кроме файлов:** плагин должен быть включён
(`plugins.enabled` в конфиге Hermes), а в `manifest.json` стоять `api: true` —
без этого `plugins/b2s/dashboard/plugin_api.py` не смонтируется под
`/api/plugins/b2s/`. Проверка живости маршрута:

```bash
python tools/probe_route.py skills      # 200 + контрольный 404 = смонтирован
```

## `config.json`

Машинно-специфичный: `fork` — путь к этому репозиторию, `python` — интерпретатор
profиля (`hermes-agent/venv/Scripts/python.exe`) для гейтов. Переопределяется
переменными `B2S_FORK` / `B2S_PYTHON`. При переносе профиля на другую машину
правится руками.

## Чего тут нет и почему

- Файлы вне `desktop-plugins/` и `plugins/` (включая этот README) — документация
  зеркала: синхронизатор их не копирует, в профиле им делать нечего.
- `b2s_fetched/`, `staging/`, `backups/` — рабочие продукты, в `.gitignore`:
  скачанный текст может быть защищён авторским правом, а бэкапы скиллов —
  локальная страховка.
- Штатный конфиг Hermes (`config.yaml`) — не наша зона: единственное, что от
  нас нужно, — включённый плагин `b2s`.
