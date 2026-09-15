/**
 * b2s — панель BOOK-TO-SKILL в правом rail'е Hermes desktop.
 *
 * Дисковой контракт плагина: ESM, только @hermes/plugin-sdk + react +
 * react/jsx-runtime, jsx()/jsxs() вместо JSX (файл не компилируется).
 *
 * Два канала, по природе работы:
 *   • ctx.rest('/rerun' | '/install' | '/desc') — REST в свой backend-namespace
 *     (/api/plugins/b2s/…), который монтирует `dashboard/plugin_api.py`.
 *     Это ДЕТЕРМИНИРОВАННЫЕ шаги: загрузка источника, очистка, отчёт,
 *     предпросмотр переноса. LLM тут не нужен, чат не участвует, токены не
 *     жгутся — поэтому они идут сюда, а не «событием в чат».
 *   • host.request('prompt.submit') — в активную сессию чата. Только там,
 *     где без LLM нельзя: проза глав (draft) и разбор черновика (review).
 *
 * Фоллбэк: если REST недоступен (маршруты не смонтированы — dashboard ещё не
 * перезапускался), кнопки честно говорят об этом и уходят прежним путём в чат,
 * чтобы работа не встала.
 */

import {
  Badge,
  Button,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  StatusDot,
  cn,
  host,
  useValue
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

const STRATEGIES = [
  { value: 'auto', label: 'auto - каскад стратегий' },
  { value: 'raw-md', label: 'raw-md - источник уже markdown' },
  { value: 'trafilatura-html', label: 'trafilatura - основная очистка' },
  { value: 'bs4-html', label: 'bs4 - добор хвоста и таблиц' },
  { value: 'stdlib-html', label: 'stdlib - крайний случай' }
]

const MODES = [
  { value: 'technical', label: 'technical - таблицы и код' },
  { value: 'text', label: 'text - проза' }
]

const LANGS = [
  { value: 'ru', label: 'Русский' },
  { value: 'en', label: 'English' },
  { value: 'source', label: 'как в источнике' }
]

/* Что делать, когда имя скилла уже занято. Галочка «многостраничное добавление»
   здесь была бы лишней: режим — следствие состояния (имя занято), а не тумблера,
   который при первом заходе всё равно ничего не значит. */
const ACTS = [
  { value: 'auto', label: 'долив - дополнить существующий' },
  { value: 'replace', label: 'замена - снести и залить заново (бэкап)' }
]

/** Файлы плана установки — ПО СТРОКЕ НА ФАЙЛ, а не склейкой в одну строку.
 *
 * Раньше список собирался как «＋ добавится (16): a, b, c …» и обрезался
 * многоточием: владелец видел «+ добавится (16): …» и не мог прочитать, что именно
 * ляжет в профиль. Знак впереди — действие над файлом (он же несёт смысл группы),
 * а полное имя файла уходит в `title` — там его видно целиком наведением. */
function planFileRows(out) {
  const plan = (out && out.plan) || {}
  const rows = []
  const put = (mark, list) => (list || []).forEach((f) => rows.push({ mark, file: String(f) }))
  put('＋', plan.added)
  put('⟳', plan.overwrite)
  put('＝', plan.keep)
  return rows
}

/** Итоги плана — то, что стоит в НИЖНЕЙ части окна, под списком файлов: счётчики
 *  по группам, журнал источников, что уже внесено. Здесь сводка одной строкой
 *  уместна: это цифры, а не список к чтению. */
function planTotalRows(out) {
  const plan = (out && out.plan) || {}
  const rows = []
  const added = (plan.added || []).length
  const over = (plan.overwrite || []).length
  const keep = (plan.keep || []).length
  const bits = []
  if (added) bits.push('добавится файлов: ' + added)
  if (over) bits.push('перезапишется: ' + over)
  if (keep) bits.push('останется как есть: ' + keep)
  if (bits.length) rows.push(bits.join(' · '))
  /* Что уже внесено в скилл: долив «органичен» только когда это видно ДО клика.
     Без журнала вторая страница вливается вслепую, и в скилле не остаётся следа,
     откуда взята та или иная глава. */
  const known = (out && out.existing_sources) || []
  if (known.length) {
    rows.push('⌸ уже внесено источников (' + known.length + '): ' + known.slice(0, 3)
      .map((s) => s.src || '?').join(', ') + (known.length > 3 ? ' …' : ''))
  }
  const jr = (out && out.journal) || null
  if (jr && jr.file) {
    rows.push((jr.new_source ? '✚ журнал: источник записан впервые'
      : '⟳ журнал: источник уже был - обновлён') +
      ' · всего источников: ' + jr.sources + ' · установок этого: ' + jr.installs)
  }
  return rows
}

/* Раскладка по главам (ответ /plan) человеческими строками. Владельцу нужен
   ответ на вопрос «что из нового источника слить со старыми главами, а что
   писать заново» — по файловому плану этого не видно: там только имена. */
function chapterRows(out) {
  const rows = []
  const act = { merge: '⇄ слить', rewrite: '⟳ переписать', add: '＋ новая' }
  const list = (out && out.chapters) || []
  list.forEach((r) => {
    const tag = act[r.action] || String(r.action || '?')
    const where = r.action === 'add' ? '' : ' → ' + (r.merge_into || '')
    const weak = r.action === 'merge' && r.confidence !== 'high' ? ' · пересечение слабое' : ''
    rows.push(tag + ': ' + r.file + where + weak)
  })
  /* Файл-цель слияния меняется, хотя в черновике его нет: в «не трогаем» ему
     нельзя — панель обязана сказать, что именно будет дополнено. */
  const touched = (out && out.touched) || []
  if (touched.length) {
    rows.push('✎ будет дополнен (' + touched.length + '): ' +
      touched.slice(0, 3).map((t) => t.file).join(', ') + (touched.length > 3 ? ' …' : ''))
  }
  const keep = (out && out.keep) || []
  if (keep.length) {
    rows.push('＝ не трогаем (' + keep.length + '): ' + keep.slice(0, 4)
      .map((k) => k.file).join(', ') + (keep.length > 4 ? ' …' : ''))
  }
  return rows
}

/** Отпечаток ВХОДОВ разбора источника. Разбор зависит не только от URL: стратегия,
 *  режим, имя и категория едут в ядро тем же запросом, поэтому «отчёт свежий» - это
 *  совпадение ВСЕГО набора. Чистая функция: проверяется в node без React.
 *  Разделитель \u0001: имена файлов/URL его не содержат, склеить два разных набора
 *  в один отпечаток нельзя. */
/** Отпечаток входов РАЗБОРА источника: по нему панель понимает, что отчёт в блоке 2
 *  снят именно с этих входов, а не с прежних. Имя скилла и категория сюда НЕ входят:
 *  это реквизиты записи, на разбор страницы они не влияют. Их правка не имеет права
 *  объявлять живой отчёт устаревшим - иначе «один чих, и заново делай черновик». */
function analysisSigOf(f) {
  const o = f || {}
  return [o.src, o.strat, o.mode]
    .map((v) => String(v == null ? '' : v).trim())
    .join('\u0001')
}

/** Скиллы ВЫБРАННОЙ категории — по алфавиту.
 *
 * Список имён обязан подчиняться категории: иначе панель подсказывает имена из чужих
 * каталогов, и человек ставит скилл не туда, куда смотрел (владелец: «выбрали категорию
 * apple - значит в списке должны быть только те, кто входит в каталог apple»).
 * Скиллы, лежащие прямо в `skills/` (категория ""), в список не попадают вовсе. */
function skillsInCat(skills, cat) {
  const want = String(cat || '')
  if (!want) return []          // категория не выбрана — подсказывать нечего
  return (skills || [])
    .filter((s) => String((s && s.category) || '') === want)
    .sort((a, b) => String(a.name).localeCompare(String(b.name)))
}

/** Заголовок свёрнутого спойлера «План по главам» — тот же принцип, что у блока
 *  черновика: в свёрнутом виде видно ФАКТ, а не одно название. Числа берём из самой
 *  раскладки, ничего не пересчитывая: сколько глав и что с ними собираются делать. */
function chapterBitsOf(out) {
  const list = (out && out.chapters) || []
  if (!list.length) return []
  const cnt = { merge: 0, rewrite: 0, add: 0 }
  list.forEach((r) => { if (cnt[r.action] != null) cnt[r.action] += 1 })
  const bits = [plural(list.length, 'глава', 'главы', 'глав')]
  if (cnt.merge) bits.push('⇄ слить ' + cnt.merge)
  if (cnt.rewrite) bits.push('⟳ переписать ' + cnt.rewrite)
  if (cnt.add) bits.push('＋ новых ' + cnt.add)
  bits.push(out.target_exists
    ? 'долив в ' + out.category + '/' + out.name
    : 'новая папка - сливать не с чем')
  if (out.threshold != null) bits.push('порог ' + out.threshold)
  return bits
}

/* Состав черновика человеческими строками (ответ /draft). Владельцу нужно видеть,
   ИЗ ЧЕГО состоит скилл до установки: шапка, главы, справочные части. Порядок
   не алфавитный, а смысловой — SKILL.md, части, главы: так состав читается с
   первого взгляда. Функция чистая (без React) — формат строк проверяют тесты. */
function draftRows(draft) {
  const files = (draft && draft.files) || []
  const order = { index: 0, part: 1, chapter: 2, other: 3 }
  const sorted = files.slice().sort((a, b) => {
    const oa = order[a.kind] != null ? order[a.kind] : 9
    const ob = order[b.kind] != null ? order[b.kind] : 9
    if (oa !== ob) return oa - ob
    return String(a.rel).localeCompare(String(b.rel))
  })
  return sorted.map((f) => ({
    rel: f.rel,
    kind: f.kind,
    label: f.rel + ' · ' + f.lines + ' стр · ' + f.chars + ' симв'
  }))
}

/* Правило панели: ЛЮБАЯ подпись объекта (путь, URL, имя файла, строка плана,
   подпись поля, шапка блока с данными) — всегда одна строка, лишнее режется
   многоточием. Перенос такой подписи распирает бокс и ломает раскладку соседей,
   а полный текст читается наведением — он уходит в title.
   Возвращаем props, а не готовый элемент: в списках нужен key, и его даёт
   вызывающий — `jsx('div', Ell(line), 'plan-' + i)`. */
/* Инлайн-страховка к классу `truncate`. Класс один не спасает: у flex-item по
   умолчанию `min-width: auto`, поэтому подпись не сжимается, `overflow: hidden`
   не срабатывает — и текст рисуется ПОВЕРХ соседей, а не режется.
   `min-width: 0` + собственный `overflow: hidden` обязательны. */
const CUT = {
  minWidth: 0,
  display: 'block',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap'
}
const Ell = (text, cls, tip) => ({
  className: cls ? 'truncate ' + cls : 'truncate',
  style: CUT,
  /* `tip` — что сказать в нативном тултипе. По умолчанию это сам текст (у прозы и
     подписей название и есть смысл), а у КНОПОК подставляется действие: наведение
     обязано отвечать «что будет, если нажму», а не перечитывать саму подпись. */
  title: String(tip == null ? text : tip),
  children: text
})
/* Готовая обрезаемая подпись — для мест, где нужен элемент (внутри кнопок).
   Третий аргумент — тултип: подпись кнопки и её описание — разные вещи. */
const cutSpan = (text, cls, tip) => jsx('span', Ell(text, cls, tip))

/* Кнопка SDK изнутри — `inline-flex shrink-0 whitespace-nowrap`, и `shrink-0`
   из её базового класса не перебить классовым `min-w-0`: оба класса живут в
   одном слое CSS, и побеждает тот, что ниже в таблице (проверено стендом на
   реальном CSS приложения: кнопка шага при панели 200 px держала 215 px и
   уезжала за контейнер на 23 px, подпись — вместе с ней, `clip0`). Режет
   только ИНЛАЙН: `flex-shrink: 1` + `min-width: 0` на кнопке. */
const BTN_FIT = { minWidth: 0, flexShrink: 1, maxWidth: '100%' }

/* Значение комбобокса. Radix `SelectValue` рендерит голый span и НАМЕРЕННО выбрасывает
   из props `className` и `style` (проверено на живом DOM плагина: `title` и `data-*`
   доезжают, классы и стили — нет, см. references/desktop-plugin-pane.md). Поэтому
   обрезаем не его, а свой span ВОКРУГ: триггер — flex, наш span — сжимаемый
   flex-item (`min-width: 0` + `flex: 1 1 auto`), внутри лежит Radix-овский
   inline-span, и многоточие рисует родитель. Замер до фикса (панель 280 px):
   значение 179 px внутри кнопки 124 px, текст выезжал наружу на 55 px. */
const vFit = (node, label) =>
  jsx('span', {
    className: 'truncate',
    style: { minWidth: 0, flex: '1 1 auto', display: 'block' },
    title: label,
    children: node
  })
/* Подпись выбранного значения — для title: у Radix в DOM лежит только value. */
const labelOf = (list, value) => {
  const hit = (list || []).find((x) => x.value === value)
  return hit ? hit.label : String(value == null ? '' : value)
}

/* Ярлыки кнопок поля «Источник» — цветные эмодзи, а не инлайновый SVG.
   Монохромные иконки владелец забраковал («ярлыки не цветные… раздражают»):
   в панели нужен узнаваемый ЦВЕТНОЙ ярлык, а `currentColor` даёт серую линию.
   Кегль и lineHeight фиксируем инлайном: без них эмодзи в кнопке 24×24 плывёт
   по базовой линии и раздувает строку поля. Полные подписи («Выбрать файл»,
   «Вставить из буфера обмена») в узкую панель не влезают — они уходят в title
   (наведение) и aria-label (озвучка). */
const EMOJI_FIT = { fontSize: 13, lineHeight: 1, display: 'block' }
const emojiGlyph = (ch) => jsx('span', { style: EMOJI_FIT, 'aria-hidden': 'true', children: ch })
const iconFolder = () => emojiGlyph('📂')
const iconClipboard = () => emojiGlyph('📋')

/* Имя файла из пути — для человеческого тоста («Источник — файл: pathlib.html»),
   при этом полный путь остаётся в detail. */
const baseNameOf = (p) => String(p || '').split(/[\\/]/).filter(Boolean).pop() || String(p || '')

/* Полный путь от «Выбрать файл» — только если это правда путь, а не имя файла.
   Мост Electron отдаёт путь, но если его нет (старая сборка), браузер вернёт
   имя — и подставлять его молча нельзя: ядро уйдёт искать файл в cwd. */
const looksLikePath = (s) => typeof s === 'string' && /[\\/]/.test(s.trim()) && s.trim().length > 1

/* Заголовок блока «Результат разбора» — то, что видно в СВЁРНУТОМ виде.
   Правило владельца: «если будет выведено "XXX симв. 0 ошибок 0 мусора", то и
   зачем туда заглядывать» — значит заголовок обязан нести факт последнего
   разбора и
   признак свежести, иначе он врёт (раньше он собирался из `history[0]` и вечно
   показывал «готово · trafilatura · 55938 симв» независимо от того, был разбор
   или нет). Состояния разведены явно: не производился / идёт / провал / готово /
   готов, но по ДРУГОМУ источнику (в поле источника уже другой URL — цифры к нему
   не относятся).
   Функция чистая и живёт на модульном уровне специально: её гоняет
   tools/tests/test_pane_head.py в node на живом файле. */
/* Готовый markdown или исходник под разбор? Это разные пути: у .md разметка
   заголовков уже на месте, и «Анализ источника» — лишний экран, поэтому блок 2
   панели пропускается. Признак берём из СТРОКИ, без сети: решение обязано быть
   видно до нажатия «ДАЛЕЕ». Query (`?raw=1`) и якорь (`#section`) не мешают —
   смотрим только путь. `.txt` СОЗНАТЕЛЬНО не markdown: разметки заголовков там
   нет, главы в нём ищет ядро, поэтому отдельный разбор ему нужен (решение
   владельца: «txt это далеко не md»). */
const MD_EXT = /\.(md|markdown)$/i
const stripQuery = (s) => String(s || '').trim().split(/[?#]/)[0]
const isRemoteSrc = (s) => /^[a-z][a-z0-9+.-]*:\/\//i.test(String(s || '').trim())
const srcIsMarkdown = (s) => {
  const v = String(s || '').trim()
  if (!v) return false
  if (!isRemoteSrc(v)) return MD_EXT.test(stripQuery(v))
  let p = stripQuery(v)
  try { p = new URL(v).pathname || p } catch (err) { /* кривой URL — судим по строке */ }
  return MD_EXT.test(stripQuery(p))
}

const fmtInt = (n) => (typeof n === 'number' ? n.toLocaleString('ru-RU') : String(n))
const headBitsOf = ({ report, src, tone, busy }) => {
  const bits = []
  const want = (src || '').trim()
  const have = report ? String(report.url || report.src || '').trim() : ''
  const stale = !!want && !!have && want !== have
  if (tone === 'error') bits.push('⚠ ошибка')
  else if (busy) bits.push('⏳ ' + busy)
  else if (!report) bits.push('разбор не производился')
  else if (stale) bits.push('отчёт по другому источнику - прогони источник заново')
  else bits.push('разбор готов')
  if (report && !busy && tone !== 'error') {
    if (report.chars != null) bits.push(fmtInt(report.chars) + ' симв')
    if (report.est_tokens != null) bits.push('~' + fmtInt(report.est_tokens) + ' токенов')
    if (report.junk_total != null) bits.push('мусор ' + report.junk_total)
    /* «0 ошибок» — не украшение: это тот бит, из-за которого в блок не заходят.
       Источник — провалы каскада (attempts с ok:false); отдельного поля errors
       отчёт фетчера не несёт, но если оно появится — берём его. */
    const tries = (report.attempts || []).length
    const fails = (report.attempts || []).filter((a) => a && a.ok === false).length
    const errs = report.errors != null ? report.errors : (tries ? fails : null)
    if (errs != null) bits.push('ошибок ' + errs)
    if (report.at) bits.push('в ' + report.at)
  }
  return bits
}

/** Склонение по-русски: 1 файл / 2 файла / 5 файлов. */
const plural = (n, one, few, many) => {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

/** Заголовок блока «Черновик скилла» — тот же принцип, что у «Результата разбора»:
 *  блок СВЁРНУТ, поэтому все цифры живут в summary, и внутрь не надо заходить
 *  («видно, что черновик есть, сколько в нём глав и объёма — и ладно»).
 *
 *  Черновик пишет LLM в ЧАТЕ, а не ядро: панель узнаёт о нём только с диска,
 *  поэтому состояния разведены явно — «черновика нет: его делает кнопка в этом
 *  блоке», «черновик другого имени» (в поле одно имя, в staging другое),
 *  «⏳ пишется» (идёт генерация: цифры прошлого черновика к ней не примешиваем).
 *
 *  Ссылок на «шаг 2» тут быть не может: блок 2 — это АНАЛИЗ источника, и при
 *  готовом markdown он пропускается, так что такая подпись отправляла человека
 *  в тупик. */
function draftBitsOf({ draft, want, busy }) {
  const bits = []
  if (busy === 'draft') {
    bits.push('⏳ пишется - прозу пишет LLM в чате')
    return bits
  }
  if (!draft) {
    bits.push('состояние черновика не прочитано')
    return bits
  }
  if (!draft.has_draft) {
    bits.push(want ? 'черновика «' + want + '» в staging нет' : 'черновика нет')
    bits.push('черновик делает кнопка ✎ в этом блоке')
    return bits
  }
  /* Каталог найден - это ещё НЕ «черновик готов»: агент пишет файлы по одному, и
     пока он не положил маркер готовности (READY.json), в staging лежит обрывок.
     Раньше панель считала готовностью первый же файл и открывала «ДАЛЕЕ» и запись
     в профиль на середине работы. Владелец: «прыткий пользователь перейдёт к блоку 5
     и запишет обрезанный скилл». */
  if (draft.writing) {
    const wc = draft.counts || {}
    bits.push('⏳ черновик пишется')
    if (wc.files != null) bits.push('уже ' + wc.files + ' ' + plural(wc.files, 'файл', 'файла', 'файлов'))
    if (wc.chapters) bits.push(wc.chapters + ' ' + plural(wc.chapters, 'глава', 'главы', 'глав'))
    bits.push('кнопки записи ждут готовности')
    return bits
  }
  bits.push('черновик готов')
  /* Шапка SKILL.md — не «мелкий недочёт»: без неё Hermes не увидит description
     и не подгрузит скилл никогда. Это ровно тот факт, ради которого не заходят
     внутрь блока, поэтому он идёт в заголовок. */
  if (draft.skill && draft.skill.frontmatter === false) {
    bits.push('⚠ шапка SKILL.md не распознана')
  }
  if (draft.name && want && draft.name !== want) {
    bits.push('это «' + draft.name + '», а в поле «' + want + '»')
  }
  const c = draft.counts || {}
  if (c.files != null) bits.push(c.files + ' ' + plural(c.files, 'файл', 'файла', 'файлов'))
  if (c.chapters) bits.push(c.chapters + ' ' + plural(c.chapters, 'глава', 'главы', 'глав'))
  if (c.chars != null) bits.push(fmtInt(c.chars) + ' симв')
  if (draft.glossary_terms) bits.push(draft.glossary_terms + ' терминов')
  if (draft.at) bits.push('в ' + draft.at)
  return bits
}

/* Блок-шаг панели: шапка-переключатель (номер, имя, состояние) + содержимое +
   футер, где справа стоит главная кнопка блока, а слева — причина, по которой
   она пока не активна. Блоки сворачиваются кликом по шапке: владелец хотел
   видеть только тот шаг, на котором стоит, и разворачивать нужный руками.
   Компонент не знает ни про ядро, ни про REST — только раскладка, поэтому его
   можно проверять без моков (как Field и Ell). */
function PaneBlock({ n, title, state, tone, open, onToggle, hint, foot, style, children }) {
  const edge =
    tone === 'done' ? '#22c55e' : tone === 'bad' ? '#dc2626' : tone === 'skip' ? 'currentColor' : null
  return jsxs('section', {
    className: 'flex min-w-0 flex-col rounded-md border',
    style: Object.assign(
      {
        borderColor: edge
          ? 'color-mix(in srgb, ' + edge + ' 45%, transparent)'
          : 'color-mix(in srgb, currentColor 20%, transparent)',
        backgroundColor: tone === 'done' ? 'color-mix(in srgb, #22c55e 7%, transparent)' : 'transparent'
      },
      style || {}
    ),
    children: [
      jsxs('div', {
        className: 'flex min-w-0 cursor-pointer select-none items-center gap-1 rounded-md px-2 py-1',
        role: 'button',
        'aria-expanded': open ? 'true' : 'false',
        title: (open ? 'свернуть: ' : 'развернуть: ') + n + '. ' + title,
        onClick: onToggle,
        children: [
          jsx('span', {
            className: 'shrink-0 text-[0.625rem] opacity-60',
            'aria-hidden': 'true',
            children: open ? '▾' : '▸'
          }),
          cutSpan(n + '. ' + title, 'text-[0.6875rem] font-medium'),
          state ? cutSpan(' · ' + state, 'text-[0.625rem] opacity-80') : null
        ]
      }),
      open ? jsx('div', { className: 'flex min-w-0 flex-col gap-2 px-2 pb-1', children }) : null,
      open
        ? jsx('div', {
            /* `pt-1` (4 px) - отбивка кнопки шага от содержимого блока: владелец просил
               опустить ВСЕ «ДАЛЕЕ» (и «Предпросмотр» в блоке 5) минимум на 3 px - кнопка
               стояла вплотную к тексту. Опускается весь футер, поэтому кнопка и подпись-
               причина слева остаются на одном уровне между собой. */
            className: 'flex min-w-0 items-center gap-2 px-2 pb-2 pt-1',
            children: [
              jsx('div', {
                className: 'min-w-0 flex-1',
                children: hint ? cutSpan(hint, 'text-[0.625rem] leading-snug opacity-70') : null
              }),
              foot || null
            ]
          })
        : null
    ]
  })
}

/* Главная кнопка блока — всегда в правом нижнем углу (просьба владельца).
   Подпись режется многоточием: кнопка SDK сжимается, а текст внутри неё — нет. */
function NextBtn({ label, onClick, disabled, fill, title }) {
  return jsx(Button, {
    size: 'sm',
    variant: 'ghost',
    disabled,
    onClick,
    title,
    className: 'h-7 shrink-0 justify-end text-xs text-(--ui-text-primary)',
    style: Object.assign({ backgroundColor: fill }, BTN_FIT),
    children: cutSpan(label)
  })
}

function Field({ label, hint, children }) {
  return jsxs('label', {
    className: 'flex min-w-0 flex-col gap-1',
    children: [
      jsxs('span', {
        /* Высота шапки ФИКСИРОВАНА: у полей с подписью-хинтом (`hint`, 10px) и без неё
           строка по `items-baseline` выходила разной высоты, и соседние поля вставали
           вразнобой - владелец глазами поймал «Имя скилла на 1-2 px ниже Категории».
           Теперь обе шапки ровно `h-4`, независимо от наличия хинта. */
        className: 'flex h-4 items-center gap-2',
        children: [
          jsx('span', Ell(label, 'text-[0.6875rem] text-(--ui-text-tertiary)')),
          hint
            ? jsx('span', Ell(hint, 'text-[0.625rem] text-(--ui-text-tertiary) opacity-70'))
            : null
        ]
      }),
      children
    ]
  })
}

function intentOf(kind, f) {
  const parts = ['b2s ' + kind]
  const push = (k, v) => {
    const s = String(v == null ? '' : v).trim()
    if (s) parts.push(k + '=' + s)
  }
  /* Описание категории — отдельное действие: ни имя скилла, ни стратегия, ни
     режим тут не нужны, они улетели бы агенту шумом. Текст описания едет, только
     если человек вписал его сам: иначе агент сочинит по категории. */
  if (kind === 'desc' || kind === 'desc-fix' || kind === 'desc-rewrite') {
    push('cat', f.cat)
    push('dmode', kind === 'desc-fix' ? 'fix' : (kind === 'desc-rewrite' ? 'rewrite' : 'write'))
    push('desc', f.desc)
    return parts.join(' | ')
  }
  if (kind !== 'install' && kind !== 'review' && kind !== 'plan') push('src', f.src)
  /* Ключ черновика - слаг ИСТОЧНИКА. Агент по нему кладёт черновик в staging/<слаг>,
     и тот же ключ панель шлёт в /draft и /install: имя скилла правится свободно и
     ключом быть не может. */
  push('slug', f.slug)
  push('name', f.name)
  push('strat', f.strat)
  push('mode', f.mode)
  if (kind === 'draft' || kind === 'review') push('lang', f.lang)
  push('cat', f.cat)
  /* Долив или замена. Это нужно и генерации: если скилл с таким именем уже есть,
     агент обязан не переписать его вслепую, а дописать новые главы и показать,
     что именно он собирается тронуть. */
  if (kind === 'draft' || kind === 'install' || kind === 'plan') push('act', f.act)
  return parts.join(' | ')
}

/** Строка-выжимка по отчёту прогона: стратегия, объём, мусор. */
function summaryOf(out) {
  const rep = (out && out.report) || {}
  const bits = []
  if (out && out.strategy) bits.push('стратегия ' + out.strategy)
  if (rep.chars) bits.push(rep.chars + ' симв')
  if (rep.words) bits.push(rep.words + ' слов')
  if (rep.est_tokens) bits.push('~' + rep.est_tokens + ' токенов')
  if (rep.junk_total != null) bits.push('мусор ' + rep.junk_total)
  if (out && out.seconds) bits.push(out.seconds + ' с')
  return bits.join(' · ')
}

function B2SPane({ ctx }) {
  const stored = ctx.storage.get('fields', null) || {}
  const [src, setSrc] = useState(stored.src || '')
  /* Имя скилла по умолчанию - ПУСТОЕ (или подхваченное из прошлого захода): панель
     не подставляет чужой пример вроде `python-pathlib`, иначе человек ставит скилл
     под чужим именем, не заметив. Имя придумывает он сам или агент.
     Восстановленное имя приводим к нижнему регистру: оно пришло из storage, а не
     набрано сейчас, и жёлтая подсветка «такое имя Hermes не примет» на нём читалась
     как поломка панели (владелец: «я ничего не делал, а он ругается»):
     `license-Info` → `license-info`. Введённое руками не трогаем - там подсветка честная. */
  const [name, setName] = useState(stored.name ? String(stored.name).toLowerCase() : '')
  const [strat, setStrat] = useState(stored.strat || 'auto')
  const [mode, setMode] = useState(stored.mode || 'technical')
  const [lang, setLang] = useState(stored.lang || 'ru')
  const [cat, setCat] = useState(stored.cat || 'software-development')
  const [cats, setCats] = useState(null)         // существующие категории из /categories
  const [catMeta, setCatMeta] = useState({})     // их описания: desc_state / desc / desc_raw
  const [catLoose, setCatLoose] = useState([])   // скиллы вне категорий — в списке их нет
  const [catDesc, setCatDesc] = useState(stored.catDesc || '') // описание своей категории
  const [catErr, setCatErr] = useState('')
  const [skills, setSkills] = useState(null)     // существующие скиллы из /skills
  const [skillsErr, setSkillsErr] = useState('')
  const [act, setAct] = useState(stored.act || 'auto')  // долив или замена, когда имя занято
  const [status, setStatus] = useState('')
  const [tone, setTone] = useState('idle')
  const [busy, setBusy] = useState('')
  const [core, setCore] = useState(null)         // null — неизвестно, true/false — ответ /health
  const [coreInfo, setCoreInfo] = useState(null) // тело /health: staging, python, черновики — в подсказке бейджа
  const [report, setReport] = useState(null)     // последний прогон из /state
  const [preview, setPreview] = useState(null)   // предпросмотр установки (без записи)
  /* Успешная запись: кнопка гаснет и говорит об этом словами. Владелец: «по успешному
     завершению установки кнопка должна быть неактивна и говорить "Успешно установлен"»,
     иначе она предлагает второй клик по уже выполненному действию. Сбрасывается тем же
     `dropPreview`, что и план: правка входов или черновика снова делает запись осмысленной. */
  const [installed, setInstalled] = useState(null)
  const [chapterPlan, setChapterPlan] = useState(null) // раскладка по главам (ответ /plan)
  const [chapterBusy, setChapterBusy] = useState(false)
  const [text, setText] = useState('')           // очищенный текст источника — первый экран панели
  const [textInfo, setTextInfo] = useState(null) // путь/объём/обрезано — ответ /text
  const [textBusy, setTextBusy] = useState(false)
  const [outOpen, setOutOpen] = useState(false)  // свёртка «Результат разбора» (шаг 1)
  /* Черновик скилла — своя свёртка и своя сводка. Отдельное состояние, а не
     переиспользование outOpen: у блока разбора и блока черновика разные жизни
     (разбор — Python в ядре, черновик — LLM в чате), и раскрытие одного не
     должно тащить за собой другое. */
  const [draft, setDraft] = useState(null)        // сводка из /draft (или из /state)
  /* Рабочие каталоги staging: чужие черновики и то, что переросло TTL. Панель
     показывает их списком с кнопками - «очистка при смене источника» иначе
     выглядела бы как исчезновение файлов без объяснений. */
  const [drafts, setDrafts] = useState(null)
  const [draftBusy, setDraftBusy] = useState(false)
  const [draftOpen, setDraftOpen] = useState(false)
  /* «План по главам» — своя свёртка, отдельная от черновика: у них разные жизни
     (раскладка считается Python-ом по кнопке, черновик пишет LLM в чате), и
     раскрытие одного не должно тянуть другое. По умолчанию свёрнут — как черновик. */
  const [chapterOpen, setChapterOpen] = useState(false)
  /* Список имён скиллов — свой, а не нативный `<datalist>`: тот показывает ВСЁ, что
     нашлось на диске (игнорируя категорию), а его попап не прокрутить и не стилизовать.
     Здесь только подсказка-список под полем: ввод имени остаётся свободным. */
  const [nameOpen, setNameOpen] = useState(false)
  const [draftFile, setDraftFile] = useState('')  // какой файл черновика открыт
  const [draftText, setDraftText] = useState(null) // его текст (null — не читали)
  /* Строка поля «Источник»: кнопки выбора файла и вставки из буфера стоят
     справа от инпута. Ref нужен, чтобы вернуть фокус в поле, когда Hermes не
     дал прочитать буфер (тогда единственный путь — Ctrl+V руками). */
  const srcRow = useRef(null)
  const [draftTextBusy, setDraftTextBusy] = useState(false)
  /* «Задание ушло в чат, жду файлы в staging». Пока LLM пишет прозу, шапка блока 4
     и его футер обязаны это говорить: иначе панель выглядит сломанной — «нажал, а
     ничего не произошло». Гасится вотчером (watchDraft) или по дедлайну. */
  const [draftWait, setDraftWait] = useState(false)
  /* Прошёл ли человек «ДАЛЕЕ» в блоке 4. Запись в профиль требует и готовности
     черновика, и этого факта: блок 5 открывается и кликом по заголовку, а тогда
     человек его не проходил. Владелец: «"Предпросмотр" должна активироваться только
     после нажатия "ДАЛЕЕ" в группе 4 - когда это позволит сама LLM». */
  const [readySeen, setReadySeen] = useState(false)
  /* Что именно LLM делает прямо сейчас: сессия, куда ушло задание, и подпись задачи.
     Плашка под заголовком читает занятость этой сессии - так «в работе LLM» гаснет
     ровно тогда, когда агент закончил ход, а не сразу после отправки. */
  const [llmSid, setLlmSid] = useState('')
  const [llmLabel, setLlmLabel] = useState('')

  /* Страховка к dropPreview: если входы поменяли руками (набрали другое имя, выбрали
     другую категорию, вставили другой источник, переключили режим/язык), собранный
     план записи уже не про них — предлагать «Установить» по устаревшему плану нельзя.
     Явные вызовы dropPreview стоят в действиях, этот эффект ловит всё остальное,
     включая правку полей клавиатурой. */
  /* Гасим план только на ВХОДАХ РАЗБОРА и режиме записи: другой источник, другая
     стратегия/режим/язык. Имя скилла и категорию отсюда убрал намеренно: это
     реквизиты записи, и их правка не имеет права трогать ни план, ни черновик. */
  useEffect(() => { setPreview(null) }, [src, act, mode, lang, strat])

  /* Мастер из пяти блоков: источник → анализ → категория и имя → черновик → запись. Открыт ровно
     один блок (владелец: «запускаем плагин — виден только первый»), шапка блока
     сворачивает/разворачивает его руками, а кнопка в правом нижнем углу ведёт к
     следующему шагу. Пропуск блока 2 для готового markdown — тоже переход. */
  const [openB, setOpenB] = useState({ 1: true, 2: false, 3: false, 4: false, 5: false })
  const curB = [1, 2, 3, 4, 5].find((k) => openB[k]) || 0
  const bodyRef = useRef(null)
  const bRefs = { 1: useRef(null), 2: useRef(null), 3: useRef(null), 4: useRef(null), 5: useRef(null) }
  /* Один шаг в работе — второй клик не копит вызовы (владелец: «чтобы не копить
     вызовы по тупому в очереди»). Именно ref, а не только setBusy: состояние
     обновится позже повторного клика в том же тике, а ref держит запрет сразу. */
  const busyRef = useRef(false)
  const [rerunErr, setRerunErr] = useState('')
  /* Какой набор входов ядро УЖЕ разобрало (отпечаток, не только URL). */
  const [fetchedSig, setFetchedSig] = useState('')
  /* Ключ черновика = слаг ИСТОЧНИКА (не имя скилла): его отдаёт ядро в /state и
     /rerun. По нему панель читает черновик и собирает план - правка имени скилла
     ключ не меняет, поэтому черновик при переименовании не теряется. */
  const [draftKey, setDraftKey] = useState('')
  /* Строка источника «как в поле»: и вотчер, и чтение сводки ходят в ядро через
     ref, а не через замыкание. Таймер, созданный ДО смены источника, иначе
     продолжает опрашивать прежний url и тянет в блок 4 чужой черновик (владелец:
     «ввёл новый url - а в окнах содержимое предыдущей работы»). */
  const srcRef = useRef(src)
  const nameRef = useRef(name)
  srcRef.current = (src || '').trim()
  nameRef.current = (name || '').trim()
  /* Имя правили руками? Предложенное ядром подставляем, только пока поле не
     трогали: подстановка не имеет права затирать ввод владельца. */
  const nameTouched = useRef(false)
  const [nameAuto, setNameAuto] = useState(false)
  const onlyB = (n) => setOpenB({ 1: n === 1, 2: n === 2, 3: n === 3, 4: n === 4, 5: n === 5 })
  const toggleB = (n) => setOpenB((cur) => Object.assign({}, cur, { [n]: !cur[n] }))

  /* Открытый блок обязан оказаться в поле зрения: панель длиннее окна, и переход
     без прокрутки читался бы как «кнопка ничего не сделала». */
  useEffect(() => {
    const body = bodyRef.current
    const node = bRefs[curB] && bRefs[curB].current
    if (!body || !node || typeof body.scrollTo !== 'function') return
    const top = node.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop
    body.scrollTo({ top: Math.max(0, top - 6), behavior: 'smooth' })
  }, [curB])

  const focusedId = useValue(host.state.focusedSessionId)
  /* Занятость сессии по её runtime-id: тот же сигнал, что пульс в статусбаре.
     Нужен, чтобы плашка под заголовком говорила правду: пока LLM пишет прозу,
     панель НЕ в прямом режиме, и зелёное «ядро на связи» читалось как «готово». */
  const busyMap = useValue(host.state.busyBySession)

  /* Занятое имя скилла — это не ошибка ввода, а состояние: скилл с таким именем
     уже стоит, и источник доливается в него. Отсюда и режим установки. */
  const wanted = (name || '').trim()
  /* Пустое имя - обязательный вход не заполнен: рамка поля красная (тонкая, как у
     блока 3), кнопка «ДАЛЕЕ» гаснет и называет причину. Подпись под полем при этом
     остаётся ОРАНЖЕВОЙ: владелец просил «цвет шрифта менять не следует». */
  const nameWarn = !wanted
  /* Имя, которое Hermes не примет, - тоже незаполненный обязательный вход, только
     причина другая: линза скиллов требует строчные латинские буквы/цифры и
     разделители `.-_`. Живой случай: имя `license-Info` дошло до записи, скилл лёг
     в профиль, а линза ответила `name: 'license-Info' must be lowercase` - то есть
     ошибку владелец увидел только ПОСЛЕ установки. Проверяем до записи, той же
     краской, что и пустое имя: это не заполненный вход, а не авария ядра. */
  const nameBad = !nameWarn && !/^[a-z0-9][a-z0-9._-]*$/.test(wanted)
  const existing = (skills || []).find((s) => s.name === wanted) || null

  /* Раскладка по главам привязана к ЦЕЛЕВОМУ скиллу: сменилось имя (или цель стала
     свободной - тогда слот и вовсе скрыт) - план недействителен, как и план записи.
     Зависимость - ИМЯ и сам факт «занято» (булево, а не объект): перечитывание
     списка скиллов с тем же статусом не должно стирать построенный план. */
  const targetTaken = !!existing
  useEffect(() => {
    setChapterPlan(null); setChapterOpen(false)
  }, [wanted, targetTaken])

  /* Подпись под полем «Имя скилла» - единственное место, где сказано, свободно имя или
     занято, и что не так с пустым. Цвет ставим inline (WARN_YELLOW, как рамка чипсы
     DESCRIPTION.md), а не tailwind-классом:
     палитра Tailwind в панели не подключена, класс молча ничего не
     покрасит. Жёлтым - только про пустое имя: это не ошибка ядра, а незаполненный
     обязательный вход, поэтому «внимание», а не «авария». */
  const nameNote = nameWarn
    ? 'имя скилла не может быть пустым!'
    : (nameBad
      ? 'только строчные латинские буквы, цифры, «-», «_», «.»: заглавные, пробелы и кириллицу Hermes не примет'
      : (nameAuto
      ? 'подставлено по источнику - переписать можно тут же'
      : (existing
      ? 'уже стоит: ' + (existing.category || 'без категории') + ' · глав ' + existing.chapters +
        ' · файлов ' + existing.files + ' - новые главы допишутся к нему'
      : (skills === null
        ? 'список скиллов грузится…'
        : (skillsErr ? 'списка нет - имя соберётся как новый скилл' : 'такого скилла нет - будет создан новый')))))

  // Имя занято → категорию берём у самого скилла (он лежит в своей категории),
  // а «замену» сбрасываем на долив: по умолчанию ничего не сносим.
  useEffect(() => {
    if (!existing) return
    if (existing.category) setCat(existing.category)
    setAct((cur) => (cur === 'replace' ? 'replace' : 'auto'))
  }, [existing && existing.name])

  /* Имя скилла и категория - реквизиты ЗАПИСИ, а не входы разбора и не ключ
     черновика. Поэтому их правка НЕ гасит план блока 5: план собирается локально
     и мгновенно, так что мы его ПЕРЕСОБИРАЕМ под новые реквизиты, а не выбрасываем
     (владелец: «один чих - и заново делай черновик»). Гасим только статус установки:
     он говорил про прежнее имя и категорию, значит к новым не относится. Раскладку
     по главам не трогаем вообще: она про главы черновика, реквизиты ей безразличны.
     Дебаунс 700 мс - чтобы набор имени по буквам не гонял пересборку на каждый символ. */
  useEffect(() => {
    setInstalled(null)
    if (!preview) return
    const t = setTimeout(() => { previewInstall(false) }, 700)
    return () => clearTimeout(t)
  }, [name, cat])

  useEffect(() => {
    ctx.storage.set('fields', { src, name, strat, mode, lang, cat, act, catDesc })
  }, [src, name, strat, mode, lang, cat, act, catDesc])

  /** «Выбрать файл» — нативный диалог приложения, а не возня с DOM: мост
      `window.hermesDesktop.selectPaths` (IPC `hermes:selectPaths`) отдаёт уже
      готовые ПОЛНЫЕ пути. Фоллбэк (сборка без этой двери) — `<input type=file>`
      + `getPathForFile`; там браузер может отдать только ИМЯ файла, и такое
      подставлять молча нельзя (ядро искало бы файл в cwd) — имя уходит в подсказку. */
  const SRC_FILTERS = [
    { name: 'Текст и документы', extensions: ['md', 'markdown', 'txt', 'rst', 'html', 'htm', 'pdf', 'docx', 'epub', 'json', 'csv'] },
    { name: 'Все файлы', extensions: ['*'] }
  ]
  const pickFile = async () => {
    const bridge = typeof window === 'undefined' ? null : window.hermesDesktop
    if (bridge && typeof bridge.selectPaths === 'function') {
      let picked = []
      try {
        picked = (await bridge.selectPaths({ multiple: false, filters: SRC_FILTERS, title: 'Источник' })) || []
      } catch (err) {
        host.notify({
          kind: 'error',
          message: 'Диалог выбора файла не открылся',
          detail: String(err && err.message ? err.message : err)
        })
        return
      }
      const path = picked[0] || ''
      if (!path) {
        host.notify({ kind: 'info', message: 'Диалог закрыт без выбора - источник не менялся.' })
        return
      }
      setSrc(path)
      host.notify({ kind: 'success', message: 'Источник - файл: ' + baseNameOf(path), detail: path })
      return
    }
    pickFileFallback()
  }

  /** Фоллбэк, если двери `selectPaths` в сборке нет: диалог даёт браузерный input,
      а путь вытаскивает мост `getPathForFile`. Отмена диалога не шлёт change —
      узел убираем по возврату фокуса в окно. */
  const pickFileFallback = () => {
    const el = document.createElement('input')
    el.type = 'file'
    el.accept = '.md,.markdown,.txt,.rst,.pdf,.html,.htm,.epub,.docx,.json,.csv,.mobi,.azw,.azw3'
    el.style.display = 'none'
    document.body.appendChild(el)
    const drop = () => {
      if (el.parentNode) el.parentNode.removeChild(el)
    }
    window.addEventListener('focus', () => setTimeout(drop, 800), { once: true })
    el.addEventListener('change', () => {
      const f = el.files && el.files[0]
      if (!f) {
        drop()
        return
      }
      const bridge = typeof window === 'undefined' ? null : window.hermesDesktop
      let full = ''
      try {
        if (bridge && typeof bridge.getPathForFile === 'function') full = bridge.getPathForFile(f) || ''
      } catch (err) {
        full = '' // мост есть, но путь не отдал — значит работаем как без моста
      }
      if (looksLikePath(full)) {
        setSrc(full)
        host.notify({ kind: 'success', message: 'Источник - файл: ' + (f.name || full), detail: full })
      } else {
        setSrc(f.name || '')
        host.notify({
          kind: 'info',
          message: 'Выбран файл: ' + (f.name || ''),
          detail: 'Полный путь диалог не отдал - возьми его кнопкой «Вставить из буфера обмена» или вставь в поле (Ctrl+V).'
        })
      }
      drop()
    })
    el.click()
  }

  /** «Вставить из буфера обмена» — URL страницы или путь к файлу. Читаем ШТАТНУЮ
      дверь `window.hermesDesktop.readClipboard()` (IPC `hermes:readClipboard` →
      `clipboard.readText()` в main-процессе). Это не придирка: браузерный
      `navigator.clipboard.readText()` в Electron отказывает, когда документ не в
      фокусе (ровно про это комментарий в `electron/main.ts:16996`) — из-за него
      кнопка и молчала. `navigator` оставлен только фоллбэком для сборки без моста. */
  const pasteSrc = async () => {
    const bridge = typeof window === 'undefined' ? null : window.hermesDesktop
    let raw = ''
    let via = ''
    try {
      if (bridge && typeof bridge.readClipboard === 'function') {
        raw = await bridge.readClipboard()
        via = 'мост Hermes'
      } else if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.readText) {
        raw = await navigator.clipboard.readText()
        via = 'браузер'
      } else {
        throw new Error('нет доступа к буферу: ни readClipboard, ни navigator.clipboard')
      }
    } catch (err) {
      const box = srcRow.current ? srcRow.current.querySelector('input') : null
      if (box && typeof box.focus === 'function') {
        box.focus()
        if (typeof box.select === 'function') box.select()
      }
      host.notify({
        kind: 'warning',
        message: 'Буфер обмена не прочитался',
        detail: 'Поле «Источник» в фокусе - нажми Ctrl+V. (' + String(err && err.message ? err.message : err) + ')'
      })
      return
    }
    const val = String(raw || '').trim()
    if (!val) {
      host.notify({ kind: 'info', message: 'Буфер обмена пуст - скопируй URL или путь к файлу.' })
      return
    }
    setSrc(val)
    host.notify({
      kind: 'success',
      message: 'Источник подставлен из буфера (' + via + ')',
      detail: val.length > 200 ? val.slice(0, 200) + '…' : val
    })
  }

  /** Текст источника — тот же шаг 1, но без LLM: ядро отдаёт файл из b2s_fetched.
      limit=0 — файл целиком («показать весь текст»), иначе только первый экран. */
  const loadText = async (limit = 6000) => {
    dropPreview()          // «показать весь текст» — действие блока 2: прежний план записи уже не про это
    setTextBusy(true)
    try {
      const out = await ctx.rest('/text', {
        method: 'POST',
        body: { limit },
        timeoutMs: 20000
      })
      if (out && out.ok) {
        setText(out.text || '')
        setTextInfo({ path: out.path, chars: out.chars, lines: out.lines, truncated: out.truncated })
      } else {
        setTextInfo((prev) => ({ ...(prev || {}), error: (out && out.error) || 'текст недоступен' }))
      }
    } catch (err) {
      setTextInfo((prev) => ({ ...(prev || {}), error: note(err) }))
    } finally {
      setTextBusy(false)
    }
  }

  /** Сводка черновика с диска: ядро читает staging и отдаёт цифры — дёшево и без LLM.
   *  Панель зовёт это при раскрытии блока, по кнопке «обновить» и по таймеру, пока
   *  блок открыт: черновик пишет LLM в чате, о готовности панель узнать не может —
   *  единственный честный источник правды здесь файлы в staging. */
  const loadDraft = async (silent = false) => {
    /* Тихий вызов из вотчера (silent) — это не нажатие владельца, и он не имеет права
       стирать план, только что собранный в блоке 5. Клик по кнопке — другое дело. */
    if (!silent) { setDraftBusy(true); dropPreview() }
    try {
      const out = await ctx.rest('/draft', {
        method: 'POST',
        /* Источник едет вместе с именем: черновик принадлежит ИСТОЧНИКУ, а имя
           скилла правится свободно и ключом быть не может. Ядро по src найдёт
           черновик даже после переименования. Берём из ref: к моменту тика
           таймера поле могло уехать на новый url. */
        body: { name: nameRef.current, src: srcRef.current },
        timeoutMs: 8000
      })
      if (out) setDraft(out)
    } catch (err) {
      setDraft((prev) => prev || { ok: false, has_draft: false, error: note(err) })
    } finally {
      if (!silent) setDraftBusy(false)
    }
  }

  /** Файл черновика — по клику внутри блока. limit 0 = файл целиком: SKILL.md и
   *  главы читаются глазами, а не «первым экраном», как сырой источник. */
  const loadDraftText = async (rel) => {
    dropPreview()          // открыли файл черновика — вход блока 5 изменился
    setDraftFile(rel || '')
    setDraftTextBusy(true)
    try {
      const out = await ctx.rest('/draft_text', {
        method: 'POST',
        body: { name: (name || '').trim(), file: rel || '', limit: 0, src: (src || '').trim() },
        timeoutMs: 15000
      })
      setDraftText(out && out.ok ? out : { ok: false, error: (out && out.error) || 'файл не прочитан' })
    } catch (err) {
      setDraftText({ ok: false, error: note(err) })
    } finally {
      setDraftTextBusy(false)
    }
  }

  // Проба ядра и последний прогон — тоже без LLM. Панель открылась — уже знает состояние.
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const h = await ctx.rest('/health', { timeoutMs: 8000 })
        if (!alive) return
        setCore(true)
        setCoreInfo(h || null)
        setStatus('ядро на связи - шаги 1 и 3 идут мимо чата')
        try {
          const s = await ctx.rest('/state', { timeoutMs: 8000 })
          /* Отчёт берём из /state (ядро отдаёт его целиком, кроме preview), а НЕ
             из history[0]: история — урезанная запись без junk_total, и заголовок
             блока вечно показывал «готово · trafilatura · 55938 симв» независимо
             от того, был разбор или нет. Цифры заголовка должны быть про отчёт. */
          const rep = s && s.report && Object.keys(s.report).length ? s.report : null
          if (alive && rep) {
            setReport({ ...rep, at: (s && s.report_at) || '' })
            loadText(6000)   // текст последнего прогона готов сразу, без кликов
          }
          /* Черновик из /state НЕ берём: там черновик ПОСЛЕДНЕГО ПРОГОНА ядра, а
             панель обязана говорить про то, что стоит в ПОЛЕ источника. На смене
             url эти две вещи расходятся, и блок 4 показывал файлы прежней работы
             (владелец: «ввёл новый url - а в окнах содержимое предыдущей работы»).
             Ключ и сводку ведёт resolve-эффект ниже: ключ — от поля, сводка — с
             диска по этому ключу. */
          /* Рабочие каталоги: список «рядом лежат чужие черновики» + тихая уборка
             АРХИВА при открытии (`keep: 99` оставляет лимит свежих в покое,
             работает только TTL установленных). Убранное называем в статусе:
             уборка не должна быть молчаливой. */
          if (alive && s && s.drafts) setDrafts(s.drafts)
          if (alive) pruneArchive(stored.src || '')
          if (alive && s && s.last_error) {
            setTone('error')
            setStatus('последний прогон провалился (' + (s.last_error.at || '') + '): ' +
              (s.last_error.message || 'причина неизвестна'))
          }
        } catch (err) { /* история не критична */ }
      } catch (err) {
        if (!alive) return
        setCore(false)
        setStatus('ядро не ответило: маршруты /api/plugins/b2s/ ещё не смонтированы - перезапусти dashboard-службу')
      }
    }
    load()
    return () => { alive = false }
  }, [])

  /* Ключ черновика ведёт СТРОКА ИСТОЧНИКА, а не прошлый прогон ядра. Спрашиваем
     ядро при вводе (оно отвечает локально, без сети и без LLM): /resolve отдаёт
     слаг источника — тот же, под которым лежит каталог в staging. */
  useEffect(() => {
    const asked = (src || '').trim()
    if (!asked) { setDraftKey(''); return undefined }
    let alive = true
    const t = setTimeout(async () => {
      try {
        const out = await ctx.rest('/resolve', {
          method: 'POST', body: { src: asked }, timeoutMs: 8000
        })
        if (alive && out && out.ok) setDraftKey(out.key || '')
      } catch (err) { /* ядро промолчало: ключ не трогаем, аварию назовёт статус */ }
    }, 500)
    return () => { alive = false; clearTimeout(t) }
  }, [src])

  /* Ключ сменился — значит сменился ИСТОЧНИК. Всё, что показывал блок 4 (сводка и
     текст черновика) и блок 5 (план записи, статус установки), относилось к
     прежнему: снимаем, иначе панель врёт о чужой работе. Затем тихо читаем сводку
     уже по новому ключу — она либо найдётся, либо честно скажет «черновика нет». */
  useEffect(() => {
    setDraft(null); setDraftText(null); setDraftFile(''); setInstalled(null)
    /* План по главам живёт вместе с черновиком и снимается вместе с ним: он построен
       по содержимому ПРЕЖНЕГО черновика, и при переходе к новому источнику старые
       строки висели в спойлере (владелец: «висит со старыми записями, даже при
       нажатии Сгенерировать черновик»). */
    setChapterPlan(null); setChapterOpen(false)
    dropPreview()
    if (!draftKey) return
    loadDraft(true)
    loadDrafts()
  }, [draftKey])

  /* Черновик пишет LLM в чате, а не панель: о том, что файлы появились, панель
     не узнаёт ниоткуда. Пока блок раскрыт — читаем сводку с диска раз в 4 с
     (ядро отвечает мгновенно: это staging на десяток файлов); блок свернули —
     таймер погашен, свёрнутая панель ядро не дёргает. Плюс к этому — кнопка
     «обновить с диска»: она нужна, когда смотришь на блок, не раскрывая. */
  useEffect(() => {
    if (!draftOpen) return undefined
    loadDraft(true)
    const t = setInterval(() => loadDraft(true), 4000)
    return () => clearInterval(t)
    /* Зависимость — КЛЮЧ ИСТОЧНИКА, а не имя скилла: переименование скилла
       черновик не меняет, а смена url меняет всё. С `name` таймер после смены
       источника продолжал жить и опрашивать прежний url из замыкания. */
  }, [draftOpen, draftKey])

  // Категории. По категории Hermes решает, когда подгружать скилл, поэтому выбор
  // идёт из существующих — но список не клетка: «своя категория…» заводит новую,
  // и это осознанный шаг (каталог создастся при установке, описание пишем сразу).
  // К каждой категории ядро отдаёт её описание — чипса показывает суть, а не
  // служебный текст, и предупреждает, когда Hermes описания не увидит.
  /** Категории и скиллы читаются с диска ФУНКЦИЯМИ, а не в useEffect([]) один раз.
      Панель живёт в приложении с Fast Refresh: состояние компонента при хот-релоаде
      СОХРАНЯЕТСЯ, поэтому «прочитано при монтировании» = панель до конца сессии
      показывает тот профиль, который был на момент её открытия. Так и терялась
      `networking` — каталог на диске есть, ядро его отдаёт, а в выпадашке он не
      появлялся, пока панель не переоткроют. Перечитывание при открытии списка
      делает выпадашку живой: каталог создали руками или скилл поставили — видно сразу. */
  const loadCats = async () => {
    try {
      const out = await ctx.rest('/categories', { timeoutMs: 8000 })
      const list = ((out && out.categories) || []).slice()
      setCats(list)
      setCatMeta((out && out.details) || {})
      setCatLoose((out && out.loose) || [])
      setCatErr('')
      // Категорию больше НЕ перетираем дефолтом: в поле может стоять своя, и
      // сброс стирал бы ввод на каждой загрузке. Дефолт ставим только в пустое
      // поле — прежнее поведение «исчезла из профиля» теперь даёт предупреждение
      // в чипсе, а не молчаливую подмену категории.
      setCat((cur) => (cur && cur.trim()
        ? cur
        : (list.includes('software-development') ? 'software-development' : (list[0] || cur))))
    } catch (err) {
      setCats([])
      setCatErr('список категорий недоступен: ' + note(err))
    }
  }

  // Существующие скиллы профиля: имя выбирают из списка, а не придумывают.
  // Занятое имя — не отказ, а сигнал «источник доливается в этот скилл»: панель
  // сама переключается в режим дополнения. Иначе на 600 страницах пришлось бы
  // каждый раз угадывать правильное имя, а промах создал бы второй скилл-дубль.
  const loadSkills = async () => {
    try {
      const out = await ctx.rest('/skills', { timeoutMs: 10000 })
      setSkills(Array.isArray(out && out.skills) ? out.skills : [])
      setSkillsErr('')
    } catch (err) {
      setSkills([])
      setSkillsErr('список скиллов недоступен: ' + note(err))
    }
  }

  useEffect(() => { loadCats(); loadSkills() }, [])

  const sendIntent = async (kind) => {
    if (busyRef.current) return false     // шаг уже в работе: второй клик не копит вызовы
    const sid = host.state.focusedSessionId.get()
    if (!sid) {
      setTone('error')
      setStatus('нет активной сессии - открой чат и повтори')
      return false
    }
    dropPreview()   // черновик/критика/описание категории — шаги блоков 2–4: план записи устарел
    const intentText = intentOf(kind, { src, name, strat, mode, lang, cat, act, desc: catDesc, slug: draftKey })
    busyRef.current = true
    setBusy(kind)
    try {
      await host.request('prompt.submit', { session_id: sid, text: intentText })
      /* Плашка «в работе LLM» загорается по ЗАНЯТОСТИ этой сессии, а не по факту
         отправки: задание уходит мгновенно, а агент думает минутами. */
      setLlmSid(sid)
      setLlmLabel(LLM_LABEL[kind] || '')
      setTone('sent')
      setStatus('→ ушло в чат агенту: ' + intentText)
      /* Описание категории пишет агент, а не панель: без слежения за целью красная
         рамка и кнопка «написать» остались бы в панели до её переоткрытия. */
      if (kind === 'desc' || kind === 'desc-fix' || kind === 'desc-rewrite') {
        const m = (catMeta || {})[cat]
        watchDesc(cat, (m && m.desc_state) || 'no-file')
      }
      /* Черновик пишет агент в чате: панель о готовности не узнаёт ниоткуда и до сих
         пор молчала — человек читал «черновика нет» и решал, что кнопка сломана.
         Теперь после отправки задания панель сама следит за staging. */
      if (kind === 'draft') {
        setDraftWait(true)
        /* Новая генерация обнуляет пройденный блок 4: черновик будет переписан,
           и прежнее «ДАЛЕЕ» относится уже к другому содержимому. */
        setReadySeen(false)
        /* Раскладка по главам — тоже по прежнему тексту: новая проза её отменяет. */
        setChapterPlan(null); setChapterOpen(false)
        watchDraft((name || '').trim(), (src || '').trim())
      }
      return true
    } catch (err) {
      setTone('error')
      setStatus('не доехало: ' + (err && err.message ? err.message : String(err)))
      return false
    } finally {
      busyRef.current = false
      setBusy('')
    }
  }

  const note = (err) => (err && err.message ? err.message : String(err))

  /** Опрос состояния после действия, ушедшего в чат: описание категории пишет агент,
      ядро только кладёт готовый текст в файл. Панель о результате не узнаёт никак —
      интент отправлен, и всё. Поэтому она сама следит за целевой категорией, пока её
      `desc_state` не изменится, и тогда перечитывает категории: рамка блока и кнопка
      внутри него переключаются без переоткрытия панели. Владелец: «нажал кнопку, а
      красная рамка и "Написать описание категории" остались» — это он и есть,
      непройденный refresh. Следим за целью ПО ИМЕНИ, а не за текущим выбором: пока
      думает агент, в выпадашке могли выбрать другую категорию — её состояние вотчер
      тоже обновит, потому что перечитывает весь снимок ядра. */
  const watchDesc = (targetCat, before) => {
    if (!targetCat) return
    const deadline = Date.now() + 180000     // агент отвечает минутами — трёх хватит
    let inFlight = false                     // запрос длиннее тика не копит вызовы
    const id = setInterval(async () => {
      if (inFlight) return
      inFlight = true
      try {
        const out = await ctx.rest('/categories', { timeoutMs: 8000 })
        const det = ((out && out.details) || {})[targetCat] || null
        const now = det ? (det.desc_state || 'ok') : 'no-file'
        if (now !== before) {
          clearInterval(id)
          setCats(((out && out.categories) || []).slice())
          setCatMeta((out && out.details) || {})
          setCatLoose((out && out.loose) || [])
          setTone('done')
          setStatus('описание категории «' + targetCat + '» ' +
            (now === 'ok' ? 'записано - Hermes его читает' : 'обновлено: ' + now))
        } else if (Date.now() > deadline) {
          clearInterval(id)
          setTone('error')
          setStatus('описание «' + targetCat + '» не изменилось за 3 минуты - смотри ответ агента в чате')
        }
      } catch (err) {
        if (Date.now() > deadline) {
          clearInterval(id)
          setTone('error')
          setStatus('состояние «' + targetCat + '» не перечитать: ' + note(err))
        }
      } finally {
        inFlight = false
      }
    }, 2500)
  }

  /* Уборка рабочего каталога. Черновик живёт каталогом `staging/<слаг>`, и после
     установки он оставался второй копией скилла - навсегда. Правила (выбраны
     владельцем): установленный черновик = архив с TTL, свежие держим лимитом,
     служебные `_probe*` не трогаем, и НИ ОДНО удаление не молчит - ядро называет,
     что уйдёт, а панель это печатает. Автоуборка при открытии трогает только архив
     (`keep: 99` оставляет лимит свежих в покое): недоделанный черновик чужого
     источника убирают руками, кнопкой-корзиной у его строки. */
  const loadDrafts = async () => {
    try {
      const out = await ctx.rest('/drafts', {
        method: 'POST', body: { src: (src || '').trim() }, timeoutMs: 8000
      })
      if (out && out.ok) setDrafts(out)
    } catch (err) { /* список не критичен: панель работает и без него */ }
  }

  /* Тихая уборка архива: `days: 0` = «срок как в ядре» (STAGING_TTL_DAYS). */
  const pruneArchive = async (activeSrc) => {
    try {
      const out = await ctx.rest('/prune', {
        method: 'POST',
        body: { src: activeSrc || '', keep: 99, days: 0, apply: true },
        timeoutMs: 10000
      })
      const gone = (out && out.dropped) || []
      if (gone.length) {
        setTone('idle')
        setStatus('уборка: архивные черновики убраны (' + gone.join(', ') +
          ') - они уже установлены и переросли срок')
      }
      loadDrafts()
    } catch (err) { /* уборка не критична: панель работает и без неё */ }
  }

  /* Убрать лишнее руками: ядро считает по своим правилам и называет причины -
     панель их печатает, а не пересказывает своими словами. */
  const runPrune = async () => {
    setBusy('prune')
    try {
      const out = await ctx.rest('/prune', {
        method: 'POST', body: { src: (src || '').trim(), apply: true }, timeoutMs: 20000
      })
      const gone = (out && out.dropped) || []
      setStatus(gone.length
        ? 'уборка: убрано ' + gone.length + ' (' + gone.join(', ') + ') - каталоги и их сырьё'
        : 'убирать нечего: черновики свежие и срока не переросли')
      await loadDrafts()
    } catch (err) {
      setTone('error')
      setStatus('уборка не прошла: ' + note(err))
    } finally { setBusy('') }
  }

  /* Убрать один каталог. Скилл в профиле не трогается: staging - мастерская. */
  const runDrop = async (key) => {
    setBusy('drop')
    try {
      const out = await ctx.rest('/drop', {
        method: 'POST', body: { key: key, src: (src || '').trim() }, timeoutMs: 20000
      })
      if (out && out.ok) {
        const srcs = (out.source_files || [])
        setStatus('рабочий каталог убран: ' + out.dropped + ' (' + out.files + ' файлов' +
          (srcs.length ? ' + сырьё: ' + srcs.join(', ') : '') + ')')
      } else {
        setTone('error')
        setStatus('убрать не удалось: ' + ((out && out.error) || 'причина неизвестна'))
      }
      await loadDrafts()
      await loadDraft(true)
    } catch (err) {
      setTone('error')
      setStatus('убрать не удалось: ' + note(err))
    } finally { setBusy('') }
  }

  /* Урна: убрать промежуточное разом - черновики в staging и сырьё в b2s_fetched,
     не дожидаясь TTL и лимитов (ядро: ``/purge``). Скиллы в профиле не трогаются:
     staging - мастерская, а не витрина. */
  const runPurgeAll = async () => {
    setBusy('purgeAll')
    try {
      const out = await ctx.rest('/purge', { method: 'POST', body: {}, timeoutMs: 30000 })
      if (out && out.ok) {
        const dirs = out.dirs || []
        const srcs = out.source_files || []
        resetAfterPurge()
        setTone('idle')
        setStatus('мусор убран: каталогов ' + dirs.length +
          (dirs.length ? ' (' + dirs.join(', ') + ')' : '') +
          ', файлов сырья ' + srcs.length +
          ((out.kept && out.kept.length) ? '; служебные оставил: ' + out.kept.join(', ') : ''))
      } else {
        setTone('error')
        setStatus('очистка не прошла: ' + ((out && out.error) || 'причина неизвестна'))
      }
    } catch (err) {
      setTone('error')
      setStatus('очистка не прошла: ' + note(err))
    } finally { setBusy('') }
  }

  /* Сброс панели после уборки: владелец просил оставить нетронутыми только строку
     «Источник» (если не пустая) и выбранную категорию скилла - её выбирают редко
     и терять не хочется. Всё остальное - поля, сводки, предпросмотр, план - к
     исходному состоянию, иначе панель показывала бы данные убранного черновика. */
  const resetAfterPurge = () => {
    setText(''); setTextInfo(null); setOutOpen(false)
    setReport(null); setFetchedSig(''); setRerunErr('')
    setName(''); setStrat('auto'); setMode('technical'); setLang('ru'); setAct('auto')
    setCatDesc(''); setCatErr(''); setNameAuto(false)
    setDraft(null); setDraftText(null); setDraftFile(''); setDraftOpen(false)
    setDrafts(null); setInstalled(null); setChapterPlan(null); setChapterOpen(false)
    setPreview(null); setReadySeen(false); setDraftWait(false)
    setLlmSid(''); setLlmLabel('')
    onlyB(1)
  }

  /** Черновик пишет агент в чате — панель о готовности не узнаёт ниоткуда, и раньше
      после «Сделать черновик» она молчала: человек читал «черновика нет» и решал,
      что кнопка сломана. Теперь панель сама читает staging, пока файлы не появятся
      (тот же приём, что у описания категории). Следим по ИСТОЧНИКУ (src): имя скилла
      могли поправить, пока агент писал главы, а черновик принадлежит источнику. */
  const watchDraft = (targetName, targetSrc) => {
    if (!targetName && !targetSrc) return
    /* Проза главы идёт минутами, а на крупном PDF агент ещё и долго думает над
       планом: пятнадцати минут не хватало и панель сдавалась раньше LLM.
       Тридцати хватает с запасом; при этом маркер готовности - единственный
       признак завершения, поэтому дедлайн только снимает ожидание. */
    const deadline = Date.now() + 1800000
    let inFlight = false
    const id = setInterval(async () => {
      if (inFlight) return
      inFlight = true
      let done = false
      try {
        const out = await ctx.rest('/draft', {
          method: 'POST',
          /* Ищем черновик ИСТОЧНИКА: имя скилла человек мог поправить, пока агент
             писал главы, и по одному имени панель черновик бы не нашла. */
          body: { name: targetName, src: targetSrc },
          timeoutMs: 8000
        })
        if (out && out.has_draft) {
          setDraft(out)          // прогресс виден и до готовности: файлы прибывают
          if (out.ready) {
            done = true
            setDraftWait(false)
            setTone('done')
            const c = out.counts || {}
            setStatus('черновик готов: ' + (out.name || targetName || targetSrc) +
              (c.files ? ' - ' + c.files + ' ' + plural(c.files, 'файл', 'файла', 'файлов') : '') +
              ': можно к блоку 4')
          } else {
            /* Каталог появился, но маркера готовности нет - LLM ещё пишет главы.
               Раньше панель объявляла готовность на первом же файле и отпирала
               «ДАЛЕЕ»: скилл мог уехать в профиль обрезанным. */
            const c = out.counts || {}
            setTone('working')
            setStatus('черновик пишется: ' +
              (c.files ? c.files + ' ' + plural(c.files, 'файл', 'файла', 'файлов') : 'файлы в staging') +
              (c.chapters ? ', ' + c.chapters + ' ' + plural(c.chapters, 'глава', 'главы', 'глав') : '') +
              ' - жду маркер готовности от LLM')
          }
        }
      } catch (err) { /* ещё не готов — ждём дальше, молча */ }
      finally {
        inFlight = false
        const late = Date.now() > deadline
        if (done || late) {
          clearInterval(id)
          if (!done && late) {
            setDraftWait(false)
            setTone('error')
            setStatus('черновик за 30 минут не помечен готовым (LLM ещё пишет или упал) - смотри ответ агента в чате')
          }
        }
      }
    }, 5000)
  }

  /** Шаг 1: детерминированный прогон источника. Никакого чата — прямой REST. */
  /** Шаг 1: детерминированный прогон источника — только ядро, без LLM и без чата.
   *  Возврат { ok, strategy, md } нужен кнопке «ДАЛЕЕ»: по нему она решает, можно
   *  ли идти дальше и не оказался ли «markdown» на деле HTML-страницей. */
  const runRerun = async () => {
    /* Повторный вход отсекает синхронный ref: двойной клик в один тик иначе
       отправит два REST и устроит гонку за один файл. */
    if (busyRef.current) return { ok: false, busy: true }
    busyRef.current = true
    setBusy('rerun')
    dropPreview()   // новый прогон источника — план записи по прежнему тексту недействителен
    setRerunErr('')
    setTone('working')
    setStatus('источник · загрузка и очистка…')
    try {
      const out = await ctx.rest('/rerun', {
        method: 'POST',
        body: { src, strat, mode, name, cat },
        timeoutMs: 300000
      })
      setCore(true)
      if (out.report) setReport({ ...out.report, at: out.report_at || 'сейчас' })
      const strategy = out.strategy || (out.report && out.report.strategy) || ''
      if (out.fetch_ok) {
        setTone('done')
        setStatus('источник разобран: ' + summaryOf(out))
        setFetchedSig(analysisSigOf({ src, strat, mode }))
        // Ключ черновика этого источника: по нему панель ищет черновик и собирает план,
        // и он НЕ меняется от правки имени скилла.
        if (out.draft_key) setDraftKey(out.draft_key)
        /* Имя скилла предлагаем ЗДЕСЬ, после разбора: в строке url тема часто не
           названа («.../186253740.html»), а в заголовке страницы — названа, и по
           разбору уже видно, о чём источник. Это ПРЕДЛОЖЕНИЕ для поля: пока имя
           не правили руками — подставляем; тронули — больше не трогаем. */
        if (out.suggested_name && !nameTouched.current) {
          setName(out.suggested_name)
          setNameAuto(true)
        }
        // Текст показываем сразу, из самого отчёта: он пришёл вместе с метриками.
        if (out.report && out.report.preview) {
          setText(out.report.preview)
          setTextInfo({
            path: out.report.source_file,
            chars: out.report.chars,
            lines: null,
            truncated: true,
            partial: true
          })
        }
        loadText(6000)   // уточняем из файла: весь объём, число строк. Нет маршрута — останется превью
        /* «Не HTML» = текст пришёл как текст. Для .md-источника это и есть ответ
           на вопрос «правда ли он markdown»: решил трафик, а не догадка по имени. */
        return { ok: true, strategy, md: !/html|bs4|trafilatura|stdlib|sphinx/i.test(strategy) }
      }
      const why = out.warning || 'причина неизвестна'
      setTone('error')
      setStatus('источник не разобран: ' + why)
      setRerunErr(why)
      return { ok: false, why }
    } catch (err) {
      /* Раньше здесь был тихий уход в чат (`sendIntent('rerun')`): сбой ядра
         выглядел как «отправил агенту», хотя агент этого шага не делает вовсе, а
         счёт за LLM всё равно бы рос. Теперь авария названа, и есть «Повторить». */
      setCore(false)
      setTone('error')
      const why = 'REST-ядро недоступно (' + note(err) + ')'
      setStatus(why + ' - источник ещё не прогнан')
      setRerunErr(why)
      return { ok: false, why }
    } finally {
      busyRef.current = false
      setBusy('')
    }
  }

  /* Шаг 3: предпросмотр переноса — тоже без LLM. Запись только по второму клику.
     Режим (долив/замена) едет в ядро явно: там он превращается в план
     «добавится / перезапишется / останется», и только увидев этот план,
     второй клик имеет право писать. Замена сносит каталог — поэтому при
     ней ядро сперва снимает бэкап, а панель показывает предупреждение. */
  /* Замечания пост-проверки ядра - человеческой строкой. Ядро пишет файлы ДО
     проверки, поэтому «проверка не прошла» ≠ «не записано»: панель обязана сказать и
     то, и другое, и назвать саму ошибку. Живой случай: в статусе стояло «см.
     подробности ниже», а подробностей панель не печатала - причина осталась в поле
     `validation.validate.output` и до глаз не доехала. */
  const installIssues = (out) => {
    const v = (out && out.validation) || {}
    const lines = [v.validate, v.scan]
      .filter((r) => r && r.ok === false)
      .reduce((acc, r) => acc.concat(String(r.output || r.error || '').split('\n')), [])
      .map((s) => s.trim())
      .filter((s) => /^(ERROR|WARN|✗|⚠)/.test(s))
    return lines.length ? lines.slice(0, 3).join('; ') : ((out && out.error) || 'причину ядро не назвало')
  }

  const runInstall = async (confirm) => {
    setBusy('install')
    setTone('working')
    setStatus(confirm ? 'установка · перенос в skills/…' : 'блок 5 · предпросмотр переноса…')
    try {
      const out = await ctx.rest('/install', {
        method: 'POST',
        body: { name: (name || '').trim(), cat, confirm: !!confirm, mode: act, allow_overwrite: true, cat_desc: catDesc, src: (src || '').trim() },
        timeoutMs: 180000
      })
      setPreview(out)
      const plan = out.plan || {}
      const adds = (plan.added || []).length
      const rewrites = (plan.overwrite || []).length
      const keeps = (plan.keep || []).length
      if (!confirm) {
        if (!out.has_skill_md) {
          setTone('error')
          setStatus((out.error || 'черновика в staging нет') + ' - сначала черновик в блоке 4')
        } else {
          setTone(out.risk ? 'error' : 'done')
          setStatus(
            'план (' + out.mode + '): +' + adds + ' новых, ⟳' + rewrites + ' перезапишется, ' +
            '＝' + keeps + ' останется как есть → ' + out.target
          )
        }
      } else if (out.ok) {
        setTone('done')
        setStatus(
          'установлено: ' + out.target + (out.backup ? ' · бэкап: ' + out.backup : ' · бэкап не нужен (новая цель)') +
          (out.category_desc_written ? ' · описание категории записано' : '')
        )
        /* Запись состоялась: кнопка гаснет и называет факт, а не предлагает второй клик. */
        setInstalled({ target: out.target, name: String(name || '').trim(), cat })
        /* Профиль изменился прямо сейчас: перечитываем скиллы и категории, иначе
           свежепоставленный скилл и созданная категория видны только после
           переоткрытия панели (та же болезнь, что и с пропавшей `networking`). */
        loadSkills()
        loadCats()
      } else {
        /* Ядро пишет файлы ДО проверки, поэтому `ok: false` здесь значит не «не
           записали», а «записали, а линза нашла замечания» - различить их можно по
           `dry_run`/`installed`. Раньше эта ветка ставила только статус: кнопка
           оставалась «Установить» на уже установленном скилле (второй клик пошёл бы
           доливом поверх), а факт записи в панели не отмечался. Владелец:
           «в каталоге скилл появился, а кнопка не изменилась на „Успешно установлено“». */
        const issues = installIssues(out)
        setTone('error')
        if (out.dry_run === false || out.installed) {
          setInstalled({ target: out.target || '', name: String(name || '').trim(), cat })
          loadSkills()
          loadCats()
          setStatus('установлено в ' + (out.target || '') + ', но проверка нашла замечания: ' + issues +
            ' - скилл уже лежит в профиле, поэтому кнопка погашена: повторный клик начал бы долив')
        } else {
          setStatus('не записано: ' + issues)
        }
      }
    } catch (err) {
      setTone('error')
      setStatus('REST-ядро недоступно (' + note(err) + '): установка не выполнена')
    } finally {
      setBusy('')
    }
  }

  /* Любое действие в блоках 1–4 обесценивает собранный план записи: он построен по тем
     самым входам (источник, имя, категория, режим, черновик), которые это действие
     меняет. Владелец: «любое нажатие кнопок в блоках 1,2,3,4 — должны сбрасывать флаг
     этой кнопки в блоке 5 и скрывать поле предпросмотра». Иначе панель предлагает
     «Установить» по устаревшему плану. */
  const dropPreview = () => { setPreview(null); setInstalled(null) }

  /* План долива по главам. Файловый план (planBlock) говорит, ЧТО ляжет, но не
     отвечает, что слить со старыми главами, а что писать заново — для этого
     нужна близость тем, её считает Python. Запись в скилл тут невозможна:
     план кладётся рядом с черновиком, агент читает его файлом. */
  const runPlan = async () => {
    dropPreview()   // раскладка по главам — шаг блока 4: план записи собирается заново
    setChapterBusy(true)
    setTone('working')
    setStatus('блок 4 · раскладка по главам…')
    try {
      const out = await ctx.rest('/plan', {
        method: 'POST',
        body: { name, cat, mode: act, save: true, src: (src || '').trim() },
        timeoutMs: 60000
      })
      setChapterPlan(out)
      const counts = (out && out.counts) || {}
      const saved = out && out.saved ? ' · план: ' + out.saved : ''
      if (!out || !out.ok) {
        setTone('error')
        setStatus('план по главам не построен: ' + ((out && out.error) || 'ядро не ответило'))
      } else if (!out.target_exists) {
        setTone('done')
        setStatus('скилла «' + name + '» ещё нет - все ' + ((out.chapters || []).length) +
          ' файлов лягут новыми, сливать не с чем' + saved)
      } else {
        setTone('done')
        setStatus('раскладка: ＋' + (counts.add || 0) + ' новых, ⇄' + (counts.merge || 0) +
          ' слить, ⟳' + (counts.rewrite || 0) + ' переписать' + saved)
      }
    } catch (err) {
      setTone('error')
      setStatus('REST-ядро недоступно (' + note(err) + '): план по главам не построен')
    } finally {
      setChapterBusy(false)
    }
  }

  const isWorking = !!busy
  /* Точка у шапки: авария — красная, ядро молчит — жёлтая, работа/готово/ядро на связи —
     зелёная, покой — серая. Ядро отвечает всегда, кроме неразвёрнутого dashboard, поэтому
     «на связи» = зелёный: состояние видно без наведения. */
  const dotTone =
    tone === 'error'
      ? 'bad'
      : core === false
        ? 'warn'
        : isWorking || tone === 'done' || core === true
          ? 'good'
          : 'muted'

  /* Подсказка бейджа: ЧТО именно ответило. Серая плашка «ядро на связи» не сообщала ничего
     (владелец: «непонятно, что он там отображает»), а в /health лежит начинка ядра:
     где staging, какой Python, какие черновики наготове. Рассказа про «шаги 1 и 3 идут
     мимо чата» здесь НЕТ: владелец счёл его излишним - в подсказке нужны факты, а не
     лекция. */
  const coreTip = coreInfo
    ? [
        coreInfo.layer ? 'слой: ' + coreInfo.layer : '',
        coreInfo.repo ? 'репо: ' + coreInfo.repo : '',
        coreInfo.staging ? 'staging: ' + coreInfo.staging : '',
        coreInfo.python ? 'python: ' + coreInfo.python : '',
        Array.isArray(coreInfo.drafts)
          ? 'черновики (' + coreInfo.drafts.length + '): ' + coreInfo.drafts.join(', ')
          : ''
      ].filter(Boolean).join('\n')
    : 'Локальный Python ядра, без LLM.'

  /* Раскладка шагов уехала в блоки мастера (PaneBlock ниже): каждая кнопка живёт
     в своём блоке, а «ДАЛЕЕ» в правом нижнем углу ведёт от блока к блоку. */

  /* Всё про шаг 1 — сворачиваемый блок сразу под его кнопкой: сводка последнего
     прогона и очищенный текст источника.
     Блок СВЁРНУТ по умолчанию и сам не раскрывается (решение владельца: «Не надо
     его автоматически раскрывать — если будет выведено "XXX симв. 0 мусора", то и
     зачем туда заглядывать»). Факт живёт в summary — его собирает headBitsOf по
     НАСТОЯЩЕМУ отчёту из /state, — а строка статуса вынесена НАРУЖУ, потому что
     иначе клик по кнопке выглядел бы как «ничего не происходит» (эту граблю
     ловили, когда статус жил под спойлером). */
  const headBits = headBitsOf({ report, src, tone, busy })

  /* Отпечаток входов и «свежесть» отчёта. Считается здесь, а не рядом с кнопкой шага 1:
     от этого зависит и подпись спойлера «Результат разбора» (он объявлен ниже), и переход
     к черновику. Отчёт свежий только для ТОГО ЖЕ набора входов: правка в блоке 1 (источник,
     стратегия, режим, имя, категория) обесценивает разбор, как `dropPreview`
     обесценивает план в блоке 5. Иначе кнопка обещала бы «Прогнать заново» по отчёту
     от другого источника, а черновик собрался бы по чужим метрикам. */
  const trimSrc = (src || '').trim()
  const mdSrc = srcIsMarkdown(trimSrc)
  const curSig = analysisSigOf({ src: trimSrc, strat, mode })
  const analyzed = !!report && report.chars != null && fetchedSig !== '' && fetchedSig === curSig
  const staleReport = !!report && !analyzed
  /* «Блок 2 пройден» - отдельный признак, а не синоним `analyzed`: у markdown-источника
     блок 2 панель помечает «пропустим», и требовать разбор там нечего. Нужен, чтобы
     «ДАЛЕЕ» в блоках 3-5 не срабатывала В ОБХОД анализа: блоки открываются кликом по
     шапке, и владелец поймал этот обход - «в группе 3 кнопка доступна, хотя группа 2
     не пройдена». */
  const block2Passed = analyzed || mdSrc

  /* Контраст от темы без угадывания имени фонового токена: вуаль берём
     от ЦВЕТА ТЕКСТА темы. В тёмной теме текст светлый — блок выходит
     светлее фона панели, в светлой — темнее, ровно как просил владелец;
     на кастомных скинах работает так же, ветвлений по теме нет.
     Почему свой микс, а не штатные --ui-bg-*: в дизайн-системе они
     рассчитаны на hover-заливку (primary 10%, secondary 7%, tertiary 5%,
     quaternary 4% — styles.css) и как фон карточки не читаются.
     Фон самого поля текста НЕ трогаем (просьба), но рамку поля держим
     заметной отдельно: иначе прокручиваемый текст плывёт по общему фону
     и строки выглядят галлюцинирующими. */
  const BASE = 'var(--ui-base, var(--ui-text-primary, currentColor))'
  /* Плотность блока «Результат разбора» держим на уровне кнопки шага 1
     (variant secondary = --ui-bg-quaternary: акцент 5% + база 4%). Своим цветом,
     а не через токен кнопок: тему кнопок владелец будет менять, а блок не должен
     уезжать за ней. Без акцентной примеси — она в блоке теряется (5% акцента на
     тёмной панели даёт сдвиг в 2-3 единицы канала). На 5% блок сливался с фоном
     панели и читался как обычный текст (владелец: «все под одним фоном — диссонанс»),
     поэтому плотность 12% + своя граница: блок должно быть ВИДНО, а не угадывать,
     где он начинается. */
  const BLOCK_BG = 'color-mix(in oklab, ' + BASE + ' 12%, transparent)'
  const BLOCK_LINE = '1px solid color-mix(in oklab, ' + BASE + ' 30%, transparent)'
  /* Группа-бокс (описание категории, план, раскладка) НЕ растёт под перенос
     текста. При сужении панели проза ложится в 5-6 строк, и бокс вытягивается
     в «сосиску» сверху вниз (владелец: «пусть остаются размером такими как
     есть в высоту, а не пытаются уместить в себе написанный текст»). Потолок в
     em привязан к кеглю группы (10px), а не к пикселям: высота держится при
     любом шрифте темы. Инлайном — классы панели доезжают не все. */
  const GROUP_CAP = { maxHeight: '7em', overflowY: 'auto', overscrollBehavior: 'contain' }
  /* «Окно» с потолком, но БЕЗ flex-колонки. Причина не косметическая: у flex-ребёнка
     с обрезкой (`overflow: hidden`) автоматический минимум = 0, поэтому вместо прокрутки
     строки СПЛЮЩИВАЮТСЯ — замер стендом: 12 строк по 2.5 px, scrollHeight = clientHeight
     (окно врало, что переполнения нет, а строки были нечитаемы). Блочная зона отдаёт
     строки как есть, скролл честный; отступы между ними держит `space-y-1` в className. */
  const ZONE_CAP = {
    ...GROUP_CAP,
    display: 'block',
    /* Единый стандарт «окна» панели (блоки 4 и 5): потолок как у поля текста в блоке 2
       и грип в правом нижнем углу — тянешь, окно растёт. */
    maxHeight: 288,
    minHeight: '7em',
    resize: 'vertical'
  }
  /* Внутреннее окно списка файлов в плане установки — тот же стандарт «окна», что у
     зон блока 3 (ZONE_CAP): потолок 288 px, блочная раскладка, грип. Фон плагина и
     рамка поля даются на месте вызова, здесь — только раскладка. */
  const PREVIEW_CAP = { ...ZONE_CAP, overflowX: 'hidden' }
  /* Раскладка зоны текста внутри группы: строки друг под другом. */
  const GROUP_LEAD = 'flex min-w-0 flex-col gap-1'
  const FIELD_LINE = '1px solid color-mix(in oklab, ' + BASE + ' 22%, transparent)'
  /* Фон поля вычищенного текста — фон самой панели (тот же, на котором стоит
     подпись кнопки шага 1), а не наша вуаль: поле читается как «окно» в блоке,
     текст ложится на привычный фон, а границу держит рамка.
     ВАЖНО: стекло (translucency, mode=glass) включено по умолчанию, и под ним
     styles.css обнуляет --ui-*-surface-background в transparent — тогда поле
     стало бы прозрачным и показало нашу же вуаль блока (то есть «вид не
     изменился»). Поэтому поле помечено data-glass-raised: этот атрибут
     возвращает токену почти солидную заливку от --ui-bg-chrome (max 94%,
     --translucency-glass-keep), ровно как у карточек поверх стекла. Без стекла
     атрибут безвреден и даёт просто фон панели. */
  const PANEL_BG = 'var(--ui-chat-surface-background, var(--ui-bg-chrome, transparent))'
  /* Рамки полей и комбобоксов панели — заметнее штатных. Приложение рисует борт
     поля правилом `.desktop-input-chrome` (unlayered) как
     `color-mix(in srgb, var(--dt-composer-ring) var(--dt-input-border), transparent)`,
     где ring — это var(--ui-base), а knob темы равен 7 % (светлая) / 4 % (тёмная).
     На стекле такая линия почти не читается (владелец: «просто швах»), поэтому
     поднимаем knob до 22 % — плотность рамки поля «Очищенный текст источника».
     Крутим ИМЕННО knob, а не inline borderColor: unlayered-правила приложения
     проигрывают inline-стилю, и борт замер бы намертво — hover (×2) и focus/open
     (полный ring) перестали бы работать. С knob'ом состояния считает само
     приложение, а цвета остаются темными (color-mix от var(--ui-base)).
     Ставим на корень панели: все поля живут внутри неё и наследуют переменную.
     Портальные поверхности (выпадашка SelectContent, диалог) внутрь не попадают —
     у них своя тема, и это правильно: список не поле, а меню. */
  const FIELD_CHROME = { '--dt-input-border': '22%' }
  /* Заливка трёх кнопок-шагов: слабо-зелёная, своей константой. База 4% — плотность
     прежней кнопки шага 1, зелёный #22c55e — примесь сверху. Не токен темы: палитру
     кнопок владелец будет крутить, а шаги должны остаться узнаваемыми. */
  const STEP_GREEN = '#22c55e'
  const STEP_BG = 'color-mix(in srgb, ' + STEP_GREEN + ' 16%, color-mix(in srgb, ' + BASE + ' 4%, transparent))'
  /* «Критика и список правок» — единственное действие без записи: ничего не
     создаёт и не переписывает, только просит LLM разобрать черновик. Держим
     бледно-жёлтой, чтобы её не путали с зелёными шагами, которые меняют файлы. */
  const REVIEW_YELLOW = '#facc15'
  const REVIEW_BG = 'color-mix(in srgb, ' + REVIEW_YELLOW + ' 22%, color-mix(in srgb, ' + BASE + ' 4%, transparent))'
  /* Тултипы кнопок: при наведении панель рассказывает, ЧТО СДЕЛАЕТ кнопка, а не
     повторяет её подпись (владелец: «выдавать краткое описание того, что кнопки
     будут делать, вместо названия кнопок»). Формулировка — от лица действия, с
     честной ценой там, где шаг платный, и с «без записи / только чтение» там,
     где это неочевидно. */
  const TIP = {
    srcAnalyze: 'Скачать источник, вычистить мусор и посчитать метрики - прямо в ядро, без чата и без LLM',
    srcRerun: 'Разобрать источник заново: файлы скилла не пишутся, в профиль ничего не уходит',
    srcAnyway: 'Разобрать как страницу, даже если имя файла похоже на markdown',
    retry: 'Повторить разбор источника в том же режиме и с той же стратегией',
    showText: 'Показать очищенный текст источника целиком - только чтение, ничего не пишет',
    draft: 'Задание агенту в чате: написать черновик скилла в staging. В профиль ничего не пишется',
    redraft: 'Перегенерировать черновик с учётом твоих замечаний - шаг платный, счёт растёт с каждой итерацией',
    review: 'Разобрать черновик и выписать правки списком: файлы не меняются, платит только LLM в чате',
    reviewOff: 'Сначала сделай черновик - разбирать пока нечего',
    plan: 'Сравнить главы черновика с соседними скиллами: что слить, что переписать - без записи и без LLM',
    planSend: 'Отдать раскладку агенту в чат: пусть решит, что слить и что переписать',
    draftRefresh: 'Перечитать staging с диска: список файлов и их объём',
    stagingCheck: 'Проверить, не появились ли файлы в staging - только чтение',
    skillMd: 'Показать текст SKILL.md из черновика',
    openFile: 'Показать текст файла черновика: ',
    catDesc: 'Задание агенту в чате: написать description категории - файл скилла не трогается',
    catDescFix: 'Задание агенту в чате: обернуть готовый текст категории в frontmatter, тело сохранится',
    catDescRewrite: 'Задание агенту в чате: пересоздать описание категории - прежний текст будет заменён',
    prune: 'Убрать лишние рабочие каталоги: установленные старше срока и свежие сверх лимита. Сырьё уходит с ними, скиллы в профиле не трогаются',
    purgeAll: 'Очистить промежуточные результаты работы и черновики (убрать мусор)'
  }

  /* Подпись задачи для плашки «в работе LLM»: что именно пишет агент в чате.
     Одна карта на все чат-шаги, чтобы подпись не разъезжалась с кнопками. */
  const LLM_LABEL = {
    draft: 'черновик скилла',
    redraft: 'перегенерация черновика',
    review: 'критика черновика',
    plan: 'план по главам',
    desc: 'описание категории',
    'desc-fix': 'правка описания',
    'desc-rewrite': 'пересоздание описания'
  }
  /* Чипса категории — «суть категории», а не служебная подпись. Описание берём
     ровно то, что читает Hermes: `description` из DESCRIPTION.md уезжает в промпт
     рядом с именем категории (agent/prompt_builder.py:_read_category_descriptions).
     Поэтому состояний три, а не «файл есть/нет»: ok — агент описание видит;
     no-frontmatter — текст есть, но в промпт он НЕ попадает; no-file — у категории
     описания нет вообще. Границы чипсы обязательны: без них проза категории
     сливалась бы с остальными полями. */
  /* Заливку чипсе даём inline (как блокам результата): классы панели на произвольных
     токенах могут не доехать до её CSS, а тема должна работать в обеих темах и без
     переменных приложения — color-mix от BASE даёт ровный тон поверх любого фона. */
  const CHIP_BOX = 'rounded-md border px-2 py-1 space-y-1'
  const CHIP_BG = 'color-mix(in oklab, ' + BASE + ' 12%, transparent)'
  const CHIP_LINE = '1px solid color-mix(in oklab, ' + BASE + ' 30%, transparent)'
  /* Подпись над блоком отвечает «что это», вместо служебной прозы внутри блока:
     владелец попросил убрать пояснения про промпт и оставить «Содержимое DESCRIPTION.md». */
  const CHIP_LABEL = 'text-[10px] font-medium tracking-wide text-(--ui-text-tertiary, #8a8a8a)'
  /* Знаки состояния — как дорожные, но читаются РАМКОЙ, а не надписью: красный —
     файла нет у пустой/новой категории, жёлтый — предупреждение (текст без frontmatter
     ИЛИ скиллы есть, а описания нет). Своими константами: смысл знака не должен
     уезжать за темой. Слов-пугалок («STOP») в блоке нет намеренно: отсутствие
     описания у живой категории — не ошибка, а лишь потеря ориентира для агента. */
  const STOP_RED = '#dc2626'
  const WARN_YELLOW = '#f59e0b'
  const CHIP_MUTED = 'text-[10px] leading-snug text-(--ui-text-tertiary, #8a8a8a)'
  const CHIP_CHIEF = 'text-[10px] leading-snug text-(--ui-text-primary)'
  const CHIP_LINK = 'h-6 justify-start px-1 text-[10px] text-(--ui-text-primary)'
  /* Кнопка-действие внутри блока описания. Нейтральная («показать весь текст»,
     «Разобрать черновик») фон берёт идентификатором у обёртки: PANEL_BG +
     data-glass-raised + ghost-вариант, иначе hover у ghost убился бы inline-фоном.
     Лечащая («Починить файл», «Написать описание категории») — отдельный ЗЕЛЁНЫЙ токен:
     починка = хорошее действие. Зелёный взят ИЗ ТЕМЫ (--ui-green = #1f8a65 в
     светлой, #55a583 в тёмной) вместе с её производными --ui-diff-add-*, где текст
     уже подмешан к чёрному/белому ради читаемости, — поэтому кнопка едет за темой
     вместе с остальной панелью, а не спорит с ней. Своих hex нет, только запасные
     значения на случай урезанной чужой темы (иначе var() отдал бы пусто). */
  const PANEL_FILL = { backgroundColor: PANEL_BG }
  const FIX_GREEN = 'var(--ui-green, #1f8a65)'
  /* Плотность 20%, а не штатные 12% из --ui-diff-add-background: кнопка живёт поверх
     блока описания, который сам залит 12% базового цвета, и 12% зелёного на этой
     вуали читались почти как прозрачность. Цвет всё равно темо-зависимый (--ui-green),
     своя только плотность. Рамка и текст — штатные производные темы. */
  const FIX_FILL = {
    backgroundColor: 'color-mix(in srgb, ' + FIX_GREEN + ' 20%, transparent)',
    border: '1px solid var(--ui-diff-add-border, ' + FIX_GREEN + ')'
  }
  const FIX_TEXT = { color: 'var(--ui-diff-add-foreground, ' + FIX_GREEN + ')' }
  /* Кнопка SDK не сжимается и не переносится — в её базовом классе `shrink-0
     whitespace-nowrap`, а обёртка `inline-flex` без ограничения ширины. Панель
     же ужимается уже, чем подпись, и кнопка вылезала за рамку блока описания
     (замер на собранном CSS приложения: «🩹 Починить файл — обернуть текст в
     frontmatter» выезжает на 3 px при 270 px ширины панели и на 93 px при 180 px).
     Поведение, которое нужно: подпись остаётся ОДНОЙ строкой и сжимается под
     ширину блока, лишнее режется многоточием — никаких переносов. Многоточие
     даёт только блочный бокс, поэтому подпись живёт в своём `span.truncate` с
     `min-width: 0` (у flex-контейнера, каким является кнопка, `text-overflow`
     сам не работает). Обёртке — `max-w-full min-w-0`: без этого она не даст
     кнопке сжаться. Короткие подписи выглядят как раньше. */
  const CHIP_FIT = BTN_FIT
  /* Подпись в кнопке живёт в своём span.truncate: многоточие умеет только блочный
     бокс, а у flex-контейнера (какова кнопка) `text-overflow` не срабатывает.
     Общее правило подписей — у `Ell` выше. */
  /* Подпись кнопки вместе с её тултипом: fitLabel(label, TIP.key). */
  const fitLabel = (label, tip) => cutSpan(label, undefined, tip)
  const chipAction = (label, onClick, disabled, tone, tip) => {
    const fix = tone === 'fix'
    return jsx('div', {
      'data-glass-raised': '',
      className: 'inline-flex max-w-full min-w-0 rounded',
      style: fix ? FIX_FILL : PANEL_FILL,
      children: jsx(Button, {
        size: 'sm',
        variant: 'ghost',
        disabled,
        onClick,
        className: CHIP_LINK + (fix ? ' font-medium' : ''),
        style: fix ? Object.assign({}, FIX_TEXT, CHIP_FIT) : CHIP_FIT,
        children: fitLabel(label, tip)
      })
    })
  }

  /* Своя категория: её в списке нет по определению — значит её надо завести.
     Каталог создастся при установке, но описание пишем сразу: категория без
     DESCRIPTION.md рождается немой, и агент видит только её имя. */
  const isCustomCat = !!(cats && cats.length) && !cats.includes(cat)
  const catInfo = (catMeta && catMeta[cat]) || null

  /* Кнопки чипсы правят файл описания. Текст сочиняет агент (это LLM-работа),
     панель шлёт интент: kind=desc — написать, kind=desc-fix — обернуть прозу
     в frontmatter, чтобы Hermes её наконец увидел. */
  const sendDesc = (kind) => sendIntent(kind)

  /* Чипса описания категории. Показываем её ВСЕГДА, когда категория выбрана:
     «ядро про неё ничего не сказало» — это тоже состояние (описания нет), а не
     повод оставить поле немым. Порядок один: суть → состояние файла → что делать. */
  const catEmpty = !!(catInfo && catInfo.empty)
  const catSkills = catInfo && typeof catInfo.skills === 'number' ? catInfo.skills : 0
  /* Неизвестная категория (нет в ответе ядра) и пустой каталог — это «новой группы
     ещё нет», красный случай. Категория со скиллами без описания — жёлтый: скиллы
     видны и грузятся как обычно, теряется лишь ориентир для агента в промпте. */
  const catIsNew = !catInfo || catEmpty
  /* Состояние описания = рамка блока и знак перед ним. Служебной прозы больше нет:
     что это за блок, говорит подпись; читает ли его Hermes — показывает знак. */
  const catState = !catInfo ? 'no-file' : (catInfo.desc_state || 'ok')
  /* Фон блока описания — ФОН ПЛАГИНА, а не своя вуаль, и цвет состояния живёт в РАМКЕ.
     На желтоватой вуали (теплая тема) зелёные кнопки «починить» читались грязно —
     владелец: «выглядит убого», и это верно: два цветных слоя (заливка блока + кнопка)
     спорили друг с другом. Теперь проза категории лежит на привычном фоне панели,
     а состояние видно рамкой, как знак: зелёная — формат верный (Hermes описание
     читает), жёлтая — предупреждение (текст без frontmatter ИЛИ скиллы есть, а
     описания категории нет), красная — ошибка (файла нет у пустой/новой категории,
     где описание решает: в промпт уйдёт голое имя и скиллы в него положат наугад).
     Зелёный — темо-зависимый --ui-diff-add-border, жёлтый и красный — константы знаков
     (WARN_YELLOW / STOP_RED): смысл состояния не должен уезжать за темой, иначе
     warning и error станут неотличимы в чужой палитре. */
  /* Потолок высоты стоит на ЗОНЕ ТЕКСТА внутри бокса (см. GROUP_CAP и GROUP_LEAD),
     а не на самом боксе: кнопка управления живёт вне скролла — иначе при сужении
     панели она уезжает под скроллбар и её не видно. */
  const catTone = {
    backgroundColor: PANEL_BG,
    border: catState === 'ok'
      ? '1px solid var(--ui-diff-add-border, ' + FIX_GREEN + ')'
      : (catState === 'no-frontmatter' || !catIsNew)
        ? '1px solid ' + WARN_YELLOW
        : '1px solid ' + STOP_RED
  }
  const catChip = jsxs('div', {
    className: 'space-y-1',
    children: [
      jsx('div', Ell('Содержимое DESCRIPTION.md', CHIP_LABEL)),
      jsxs('div', {
        className: CHIP_BOX,
        /* Фон под стеклом (translucency, mode=glass) тема обнуляет в transparent —
           тогда блок стал бы прозрачным. data-glass-raised возвращает заливку
           от --ui-bg-chrome, как у карточек и поля очищенного текста. */
        'data-glass-raised': '',
        style: catTone,
        children: catState === 'ok'
          ? [
              jsx('div', { className: GROUP_LEAD, style: GROUP_CAP, children: [
                jsx('div', { className: CHIP_CHIEF, children: catInfo.desc }),
                catEmpty
                  ? jsx('div', { className: CHIP_MUTED, children: 'каталог пока пуст: в нём ни одного скилла' })
                  : null
              ] }),
              /* Описание есть - но прятать кнопку нельзя: переписать вывеску иногда
                 нужно (категория разрослась, текст устарел). Владелец: «кнопка не
                 должна исчезать, если описание уже есть - а вдруг надо пересоздать». */
              jsx('div', { className: 'pt-1', children:
                chipAction('♻ Пересоздать описание категории',
                  () => sendDesc('desc-rewrite'), busy === 'desc-rewrite', 'fix', TIP.catDescRewrite) })
            ]
          : catState === 'no-frontmatter'
            ? [
                jsx('div', { className: GROUP_LEAD, style: GROUP_CAP, children: [
                  jsx('div', {
                    className: CHIP_CHIEF,
                    children: '⚠ файл без frontmatter - Hermes его не читает, в промпт уйдёт голое имя категории.'
                  }),
                  jsx('div', {
                    className: CHIP_MUTED,
                    children: 'сейчас в файле: ' + ((catInfo.desc_raw || '').slice(0, 240) || '-')
                  })
                ] }),
                jsx('div', { className: 'pt-1', children:
                  chipAction('🩹 Починить файл - обернуть текст в frontmatter',
                    () => sendDesc('desc-fix'), busy === 'desc-fix', 'fix', TIP.catDescFix) })
              ]
            : catIsNew
              ? [
                  jsx('div', { className: GROUP_LEAD, style: GROUP_CAP, children: [
                    jsx('div', {
                      className: CHIP_CHIEF,
                      children: 'описания категории нет - категория новая и пустая: агент увидит только имя группы.'
                    }),
                    jsx('div', { className: CHIP_MUTED, children: 'каталог пока пуст: в нём ни одного скилла' })
                  ] }),
                  jsx('div', { className: 'pt-1', children:
                    chipAction('✍ Написать описание категории',
                      () => sendDesc('desc'), busy === 'desc', 'fix', TIP.catDesc) })
                ]
              : [
                  jsx('div', { className: GROUP_LEAD, style: GROUP_CAP, children: [
                    jsx('div', {
                      className: CHIP_CHIEF,
                      children: 'описания категории нет - агент увидит только имя группы.'
                    }),
                    jsx('div', {
                      className: CHIP_MUTED,
                      children: 'скиллов внутри: ' + catSkills + ' - они видны и грузятся как обычно; ' +
                        'описание лишь подсказывает агенту, что это за группа и куда класть новое.'
                    })
                  ] }),
                  jsx('div', { className: 'pt-1', children:
                    chipAction('✍ Написать описание категории',
                      () => sendDesc('desc'), busy === 'desc', 'fix', TIP.catDesc) })
                ]
      })
    ]
  })

  /* Строка статуса живёт ВНЕ спойлера: состояние шага обязано быть видно всегда —
     иначе клик по кнопке выглядит как «ничего не происходит» (эту граблю ловили,
     когда статус стоял ПОД спойлером), а раскрывать блок разбора владелец запретил. */
  const statusLine = status
    ? jsx('div', {
        className: cn(
          'mt-1 rounded border border-(--ui-border) px-2 py-1 text-[0.625rem] leading-snug',
          tone === 'error' ? 'text-(--ui-text-primary)' : 'text-(--ui-text-secondary)'
        ),
        /* cutSpan, а НЕ Ell: Ell отдаёт props-объект {сlassName,style,title,children},
           и в children он роняет рендер (React #31 → error-boundary плагина). */
        children: cutSpan((tone === 'error' ? '⚠ ' : '') + status)
      })
    : null

  /* ── подсказка имён скиллов: только из ВЫБРАННОЙ категории ─────────────── */
  /* Раньше здесь стоял нативный `<datalist>`: он подсовывал весь профиль подряд,
     игнорируя категорию (владелец: «выбрали категорию apple - значит в списке
     должны быть только те, кто входит в каталог apple»), а его попап нельзя ни
     прокрутить, ни стилизовать. Список стал своим: окно панели с потолком и
     скроллом (ZONE_CAP), строка на скилл, ввод имени при этом остаётся свободным. */
  const skillsOfCat = skillsInCat(skills, cat)
  const nameListBlock = jsxs('div', {
    'data-glass-raised': '',
    className: 'mt-1 min-w-0 space-y-1 rounded px-1.5 py-1',
    style: Object.assign({}, ZONE_CAP, { border: FIELD_LINE, backgroundColor: PANEL_BG }),
    title: 'Тяни за угол в правом нижнем углу, чтобы растянуть список скиллов',
    children: skillsOfCat.length
      ? skillsOfCat.map((s) => jsx(Button, {
          size: 'sm',
          variant: 'ghost',
          onClick: () => { setName(s.name); setNameOpen(false) },
          className: 'h-6 justify-start text-[0.625rem]',
          style: CHIP_FIT,
          title: 'подставить имя «' + s.name + '» - скилл уже есть, включится долив',
          children: cutSpan(s.name + ' · ' + plural(s.chapters || 0, 'глава', 'главы', 'глав') +
            ' · ' + (s.files || 0) + ' файл(ов)', undefined,
            'подставить имя «' + s.name + '» - скилл уже есть, включится долив')
        }, s.name))
      : jsx('div', Ell(skills === null
          ? 'список скиллов грузится…'
          : 'в категории «' + (cat || '…') + '» скиллов нет - имя будет новым',
          'opacity-70'))
  })
  const namePickRow = jsxs('div', {
    className: 'flex flex-wrap items-center gap-1 pt-1',
    children: [
      jsx(Button, {
        size: 'sm',
        variant: 'ghost',
        onClick: () => { loadSkills(); setNameOpen((v) => !v) },
        className: 'h-6 justify-start text-[0.625rem]',
        style: CHIP_FIT,
        title: 'Показать скиллы категории «' + (cat || '…') + '» - имя готового можно подставить, а не набирать руками',
        children: cutSpan((nameOpen ? '▴ скрыть список' : '▾ скиллы категории') +
          (skills === null ? '' : ' · ' + skillsOfCat.length), undefined,
          'Показать скиллы категории «' + (cat || '…') + '» - имя готового можно подставить, а не набирать руками')
      }),
      jsx('span', Ell(catEmpty ? 'в этой категории ещё ничего нет' : '', 'text-[10px] opacity-70'))
    ]
  })

  const resultBlock = jsxs('details', {
    className: 'rounded border px-2 py-1 text-[0.625rem] leading-snug',
    style: { backgroundColor: BLOCK_BG, border: BLOCK_LINE },
    open: outOpen,
    onToggle: (e) => setOutOpen(!!(e && e.target && e.target.open)),
    children: [
      jsx('summary', {
        className: 'cursor-pointer select-none text-(--ui-text-secondary)',
        children: jsx('span', Ell('📊 Результат разбора' + (headBits.length ? ' · ' + headBits.join(' · ') : ' - пока пусто') +
          (staleReport ? ' · от прежних входов' : '')))
      }),

      /* 2) сводка последнего прогона: стратегия-победитель, объём, путь к файлу */
      report
        ? jsxs('div', {
            className: 'mt-1 flex flex-col gap-0.5 text-(--ui-text-secondary)',
            children: [
              jsx('div', {
                children: 'последний прогон' + (report.at ? ' (' + report.at + ')' : '') +
                  (report.strategy || report.won ? ' · ' + (report.strategy || report.won) : '')
              }),
              jsx('div', {
                children: [
                  report.chars != null ? report.chars + ' симв' : null,
                  report.words != null ? ' · ' + report.words + ' слов' : null,
                  report.junk_total != null ? ' · мусор ' + report.junk_total : null
                ].filter(Boolean).join('')
              }),
              report.source_file
                ? jsx('div', Ell(report.source_file, 'opacity-80'))
                : null
            ]
          })
        : null,

      /* 3) собственно очищенный текст — то, что уйдёт в пайплайн */
      jsx('div', Ell(
        '🔤 Очищенный текст источника' +
          (textInfo && textInfo.chars != null ? ' · ' + textInfo.chars + ' симв' : '') +
          (textInfo && textInfo.lines != null ? ' · ' + textInfo.lines + ' строк' : '') +
          (textInfo && textInfo.partial ? ' · первые 6 КБ' : ''),
        'mt-2 text-(--ui-text-secondary)'
      )),
      textInfo && textInfo.error && !text
        ? jsx('div', {
            className: 'break-all pt-1 opacity-80',
            children: 'текст не отдан: ' + textInfo.error
          })
        : null,
      text || textBusy
        ? jsx('pre', {
            className: 'mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded px-1.5 py-1 text-[0.625rem] leading-snug text-(--ui-text-secondary)',
            'data-glass-raised': '',
            style: { border: FIELD_LINE, backgroundColor: PANEL_BG },
            children: textBusy && !text ? 'загружаю…' : text
          })
        : jsx('div', {
            className: 'mt-1 opacity-70',
            children: 'нажми «Прогнать источник заново» в блоке 1 - вычищенный текст появится здесь'
          }),
      textInfo && textInfo.path
        ? jsx('div', Ell(textInfo.path, 'pt-1 opacity-70'))
        : null,
      textInfo && textInfo.truncated
        /* Фон кнопки — тот же, что у текстового поля (фон панели + data-glass-raised,
           иначе под стеклом он резолвится в transparent и кнопка снова «сливается»).
           Фон задаём обёртке, а не кнопке: у ghost-варианта заливка живёт на hover,
           и inline-стиль кнопки перебил бы его — кнопка стала бы «мёртвой» на наведение. */
        ? jsx('div', {
            'data-glass-raised': '',
            className: 'mt-1 inline-flex max-w-full min-w-0 rounded',
            style: { backgroundColor: PANEL_BG },
            children: jsx(Button, {
              size: 'sm',
              variant: 'ghost',
              disabled: textBusy,
              onClick: () => loadText(0),
              className: 'h-6 justify-start text-[0.625rem]',
              style: CHIP_FIT,
              children: fitLabel('показать весь текст', TIP.showText)
            })
          })
        : null,
      jsx('div', {
        className: 'pt-1 opacity-70',
        children: 'источник всегда на языке оригинала: перевод искажал бы цитаты и имена API'
          + (textBusy ? ' · обновляю…' : '')
      })
    ]
  })

  /* Блок черновика скилла — результат шага 2, поэтому стоит сразу ПОД его кнопкой
     и по устройству повторяет «Результат разбора»: тот же details-спойлер, тот же
     фон и граница, и так же НЕ раскрывается сам (решение владельца: «схожим с
     "Результат разбора"... сворачиваться и разворачиваться»).
     Сводка в заголовке — факт с диска (файлы, главы, объём, время), по нему
     решается, надо ли вообще заходить внутрь. Внутри — состав черновика и текст
     выбранного файла; «показать всё сразу» здесь не нужно: SKILL.md и главы
     читаются по одному, а не простынёй. */
  const draftBits = draftBitsOf({ draft, want: (name || '').trim(), busy: draftWait ? 'draft' : busy })
  const draftRowsList = draftRows(draft)
  const draftBlock = jsxs('details', {
    className: 'rounded border px-2 py-1 text-[0.625rem] leading-snug',
    style: { backgroundColor: BLOCK_BG, border: BLOCK_LINE },
    open: draftOpen,
    onToggle: (e) => setDraftOpen(!!(e && e.target && e.target.open)),
    children: [
      jsx('summary', {
        className: 'cursor-pointer select-none text-(--ui-text-secondary)',
        children: jsx('span', Ell('📝 Черновик скилла' +
          (draftBits.length ? ' · ' + draftBits.join(' · ') : '')))
      }),

      ...(draft && draft.has_draft
        ? [
            /* Паспорт: имя скилла и description. По description Hermes решает,
               подгружать ли скилл, поэтому «шапки нет» — не мелочь, а состояние,
               которое обязано быть видно здесь, а не при установке. */
            jsxs('div', {
              className: 'mt-1 flex flex-col gap-0.5 text-(--ui-text-secondary)',
              children: [
                jsx('div', Ell('лежит в ' + (draft.dir || 'staging/' + (draft.name || '')) +
                  ' - в постоянные скиллы ничего не ушло')),
                jsx('div', { className: 'break-words', children:
                  'SKILL.md: ' + ((draft.skill && draft.skill.name) || draft.name || '-') +
                  (draft.skill && draft.skill.description ? ' - ' + draft.skill.description : '') }),
                draft.skill && draft.skill.frontmatter === false
                  ? jsx('div', {
                      className: 'text-(--ui-text-primary)',
                      children: '⚠ шапка SKILL.md не распознана: Hermes не увидит description и скилл не подхватится'
                    })
                  : null,
                jsx('div', { className: 'opacity-80', children:
                  'глав ' + ((draft.counts && draft.counts.chapters) || 0) +
                  ' · терминов ' + (draft.glossary_terms || 0) +
                  ' · строк ' + fmtInt((draft.counts && draft.counts.lines) || 0) })
              ]
            }),

            jsx('div', { className: 'mt-2 text-(--ui-text-secondary)',
              children: 'состав черновика - нажми файл, чтобы прочитать' }),
            /* Скроллится ТОЛЬКО список файлов: управление под ним обязано остаться
               на виду (грабля: кнопка внутри зоны с потолком уезжает под скроллбар).
               Зона — то же «окно», что поле очищенного текста в блоке разбора: фон
               панели + data-glass-raised + рамка поля. Без них список кнопок ложился
               прямо на вуаль блока и читался как её же текст (владелец: «текстовое
               поле с прокруткой должно выводиться с фоном всего плагина»); под стеклом
               токен вообще обнуляется в transparent, поэтому без data-glass-raised
               заливки не будет вовсе. */
            jsx('div', {
              'data-glass-raised': '',
              className: 'min-w-0 space-y-1 rounded px-1.5 py-1',
              style: Object.assign({}, ZONE_CAP, { border: FIELD_LINE, backgroundColor: PANEL_BG }),
              title: 'Тяни за угол в правом нижнем углу, чтобы растянуть список файлов',
              children: draftRowsList.map((r) => jsx(Button, {
                size: 'sm',
                variant: 'ghost',
                disabled: draftTextBusy,
                onClick: () => loadDraftText(r.rel),
                className: 'h-6 justify-start text-[0.625rem]',
                style: CHIP_FIT,
                title: TIP.openFile + r.label,
                children: cutSpan((draftFile === r.rel ? '▸ ' : '') + r.label, undefined, TIP.openFile + r.label)
              }, r.rel))
            }),
            jsx('div', {
              className: 'mt-1 flex flex-wrap gap-1',
              children: [
                jsx(Button, {
                  size: 'sm',
                  variant: 'ghost',
                  disabled: draftBusy,
                  onClick: () => loadDraft(),
                  className: 'h-6 text-[0.625rem]',
                  style: CHIP_FIT,
                  children: fitLabel(draftBusy ? '⟳ читаю staging…' : '⟳ обновить с диска', TIP.draftRefresh)
                }),
                jsx(Button, {
                  size: 'sm',
                  variant: 'ghost',
                  disabled: draftTextBusy,
                  onClick: () => loadDraftText(draftFile || 'SKILL.md'),
                  className: 'h-6 text-[0.625rem]',
                  style: CHIP_FIT,
                  children: fitLabel('📄 SKILL.md', TIP.skillMd)
                })
              ]
            }),
            /* Текст файла — тем же «окном», что очищенный текст источника в блоке
               разбора: фон панели + data-glass-raised, потому что под стеклом
               токен обнуляется и поле слилось бы с фоном. */
            draftText || draftTextBusy
              ? jsx('pre', {
                  className: 'mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded px-1.5 py-1 text-[0.625rem] leading-snug text-(--ui-text-secondary)',
                  'data-glass-raised': '',
                  style: { border: FIELD_LINE, backgroundColor: PANEL_BG },
                  children: draftTextBusy
                    ? 'читаю…'
                    : (draftText && draftText.ok === false
                        ? 'файл не прочитан: ' + (draftText.error || 'причина неизвестна')
                        : (draftText.text || ''))
                })
              : jsx('div', {
                  className: 'mt-1 opacity-70',
                  children: 'нажми файл в списке - текст покажется здесь (SKILL.md - кнопкой ниже)'
                }),
            jsx('div', {
              className: 'pt-1 opacity-70',
              children: 'черновик правят в чате; перегенерация - кнопка «✎» в блоке 4, а в skills/ переносит блок 5'
            })
          ]
        : [
            jsx('div', {
              className: 'mt-1 opacity-80',
              children: 'черновик пишет LLM в чате: задание отправляет кнопка «✎ Сделать черновик», файлы лягут в staging - сюда приедут сами, как только агент их допишет'
            }),
            jsx('div', {
              className: 'mt-1 flex flex-wrap gap-1',
              children: jsx(Button, {
                size: 'sm',
                variant: 'ghost',
                disabled: draftBusy,
                onClick: () => loadDraft(),
                className: 'h-6 text-[0.625rem]',
                style: CHIP_FIT,
                children: fitLabel(draftBusy ? '⟳ читаю staging…' : '⟳ проверить staging', TIP.stagingCheck)
              })
            }),
            jsx('div', {
              className: 'mt-1 opacity-70',
              children: (draft && draft.error)
                || (draft && Array.isArray(draft.drafts) && draft.drafts.length
                    ? 'в staging есть черновики: ' + draft.drafts.join(', ')
                    : 'в staging черновиков нет')
            })
          ])
    ]
  })

  /* План установки — не украшение: второй клик по шагу 3 ЗАПИСЫВАЕТ в профиль.
     Поэтому сперва видно, что именно изменится: сколько файлов добавится, какие
     перезапишутся, что останется как было, куда лёг бэкап. Раньше панель писала
     в папку молча и, что хуже, «замена» физически накладывала файлы поверх
     старых — теперь режим виден в заголовке и в самом плане. */
  const planFiles = planFileRows(preview)
  const planTotals = planTotalRows(preview)
  const planBlock = preview
    ? jsxs('div', {
        className: 'rounded border px-2 py-1 text-[0.625rem] leading-snug',
        style: {
          backgroundColor: BLOCK_BG,
          borderColor: 'color-mix(in oklab, ' + BASE + ' 22%, transparent)'
        },
        children: [
          /* Шапка и путь — снаружи окна, «как есть»: это ответ на вопросы «что за
             операция» и «куда пишем», его не нужно прокручивать. */
          jsx('div', Ell(
            (preview.dry_run === false ? '📦 Установлено · ' : '📦 План установки · ') +
              'режим ' + preview.mode + ' · ' + preview.files + ' файл(ов), ' + preview.kb + ' КБ',
            'text-(--ui-text-secondary)'
          )),
          jsx('div', Ell(preview.target, 'opacity-80')),
          preview.dry_run === false
            ? jsx('div', {
                children: '✔ записано ' + ((preview.wrote || []).length) + ' файл(ов)' +
                  (preview.validation && preview.validation.ok === false
                    ? ' · проверка скилла: есть замечания'
                    : ' · проверка скилла: ок')
              })
            : null,
          /* Замечания - ТЕКСТОМ, а не словом «есть замечания»: строка статуса обещает
             «см. подробности ниже», значит подробности обязаны быть здесь. Это ответ
             на «что пошло не так» без похода в терминал и без догадок. */
          (preview.dry_run === false && preview.validation && preview.validation.ok === false)
            ? jsx('div', {
                style: { color: WARN_YELLOW },
                children: '⚠ ' + installIssues(preview)
              })
            : null,

          /* Окно со списком файлов — по образцу поля очищенного текста в блоке 2:
             фон плагина, рамка поля, потолок 288 px (`max-h-72`) и грип в правом
             нижнем углу. Внутри — ПО СТРОКЕ НА ФАЙЛ: склейка в одну строку обрезалась
             многоточием, и что именно ляжет в профиль, прочитать было нельзя. */
          jsxs('div', {
            'data-glass-raised': '',
            className: 'mt-1 rounded px-1.5 py-1',
            title: 'Тяни за угол в правом нижнем углу, чтобы растянуть список файлов',
            style: Object.assign({}, PREVIEW_CAP, { border: FIELD_LINE, backgroundColor: PANEL_BG }),
            children: planFiles.length
              ? planFiles.map((r, i) => jsx('div', Ell(r.mark + ' ' + r.file), 'plan-file-' + i))
              : jsx('div', Ell('ядро не перечислило ни одного файла', 'opacity-70'))
          }),

          /* Низ окна — итоги и подробности, тем же порядком, что в блоке 2:
             после прокручиваемого поля идёт то, что должно быть видно всегда. */
          ...planTotals.map((line, i) =>
            jsx('div', Ell(line, i === 0 ? 'pt-1' : undefined), 'plan-total-' + i)),
          preview.mode === 'replace'
            ? jsx('div', {
                className: 'pt-1 text-(--ui-text-primary)',
                children: '⚠ ЗАМЕНА: каталог скилла сносится целиком - старых глав не останется.'
              })
            : null,
          preview.warning
            ? jsx('div', { className: 'pt-1 text-(--ui-text-primary)', children: '⚠ ' + preview.warning })
            : null,
          jsx('div', Ell(
            preview.backup
              ? 'бэкап: ' + preview.backup
              : preview.target_exists
                ? 'бэкап снимется перед записью'
                : 'новый скилл - бэкап не нужен',
            'pt-1 opacity-70'
          ))
        ]
      })
    : null

  /* Раскладка по главам — подготовка шага 3: владелец видит, что изменится в
     ТЕКСТЕ скилла, а не только какие файлы лягут. Кнопка отдельная и бесплатная:
     считает Python, в чат ничего не уходит, пока не нажмут «отправить агенту». */
  const chapterRowsList = chapterRows(chapterPlan)
  const chapterBits = chapterBitsOf(chapterPlan)
  const chapterBlock = chapterPlan
    ? jsxs('details', {
        className: 'rounded border px-2 py-1 text-[0.625rem] leading-snug',
        style: { backgroundColor: BLOCK_BG, border: BLOCK_LINE },
        open: chapterOpen,
        onToggle: (e) => setChapterOpen(!!(e && e.target && e.target.open)),
        children: [
          /* Свёрнут — но не нем: в заголовке факт с раскладки (сколько глав, что с ними
             сделают, куда долив), как у блока черновика. Разворачивается руками. */
          jsx('summary', {
            className: 'cursor-pointer select-none text-(--ui-text-secondary)',
            children: jsx('span', Ell('🧩 План по главам' +
              (chapterBits.length ? ' · ' + chapterBits.join(' · ') : '')))
          }),
          /* Скроллится только список глав: кнопка «отправить агенту» обязана
             оставаться на виду (та же раскладка, что у блока описания).
             Зона — такое же «окно» панели, как список файлов черновика: на вуали
             блока строки раскладки читались как её же текст, а под стеклом токен
             обнуляется в transparent (владелец: «дать ей такое же окно»). */
          jsx('div', {
            'data-glass-raised': '',
            className: 'mt-1 min-w-0 space-y-1 rounded px-1.5 py-1',
            style: Object.assign({}, ZONE_CAP, { border: FIELD_LINE, backgroundColor: PANEL_BG }),
            title: 'Тяни за угол в правом нижнем углу, чтобы растянуть раскладку по главам',
            children:
              chapterRowsList.map((line, i) => jsx('div', Ell(line), 'chap-' + i))
          }),
          jsx('div', {
            className: 'flex flex-wrap gap-1 pt-1',
            children: [
              jsx(Button, {
                size: 'sm',
                variant: 'ghost',
                disabled: chapterBusy,
                onClick: () => sendIntent('plan'),
                className: 'h-6 text-[0.625rem]',
                style: CHIP_FIT,
                children: fitLabel('↗ Отправить агенту: решить, что слить', TIP.planSend)
              })
            ]
          }),
          jsx('div', {
            className: 'pt-1 opacity-70',
            children: chapterPlan.target_exists
              ? 'Python дал раскладку и близость - приговор выносит LLM, спорное решает владелец'
              : 'все файлы новые: выбирать не из чего, долив невозможен'
          })
        ]
      })
    : null

  /* «План по главам» - инструмент ДОЛИВА: он показывает пересечение тем с главами
     УЖЕ СТОЯЩЕГО скилла. Если имя свободно (скилл новый), пересекать не с чем, и
     кнопка с подписью и спойлером только сбивает с толку (владелец: «следует
     показывать только если в группе 3 выясняется, что скилл уже стоит»). Признак -
     `existing`: имя занято, и блок 3 показал выбор «долив / замена». */
  const chapterSlot = !existing ? null : jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      jsx('div', {
        /* Фон обёртки = размеру кнопки. В колонке (flex-col) `align-items: stretch`
           тянул обёртку на всю ширину панели, и зелёная плашка висела пустой полосой
           (кнопка кликабельна только по тексту). `align-self: flex-start` возвращает
           фону ширину содержимого, `max-width: 100%` не даёт ей вылезти за рейл. */
        className: 'flex min-w-0 rounded',
        style: { backgroundColor: STEP_BG, alignSelf: 'flex-start', maxWidth: '100%' },
        children: jsxs(Button, {
          size: 'sm',
          variant: 'ghost',
          disabled: chapterBusy,
          onClick: runPlan,
          title: TIP.plan,
          className: 'h-7 justify-start text-xs text-(--ui-text-primary)',
          style: BTN_FIT,
          children: [
            jsx('span', { 'aria-hidden': true, style: { flexShrink: 0 }, children: '🧩' }),
            cutSpan('План по главам: что слить, что переписать', undefined, TIP.plan)
          ]
        })
      }),
      jsx('span', Ell(
        chapterBusy
          ? 'считаю близость глав…'
          : 'долив: показывает пересечение тем со старыми главами - без записи и без LLM',
        'pl-1 text-[0.625rem] leading-snug text-(--ui-text-tertiary)'
      )),
      chapterBlock
    ]
  })

  /* Строка уборки в блоке 4: что лежит рядом и что уйдёт. Молчаливых удалений в
     панели нет, поэтому «очистка при смене источника» выглядит так: список имён с
     числом файлов и пометкой «установлен», у каждого кнопка-корзина, а под ними
     одна кнопка «убрать лишнее» - она отдаёт решение ядру с его правилами. */
  const draftOthers = (drafts && drafts.others) || []
  const draftStale = (drafts && drafts.droppable) || []
  const cleanupSlot = (draftOthers.length || draftStale.length)
    ? jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('span', Ell(
            'рабочие каталоги в staging: рядом ' + draftOthers.length + ', под уборку ' +
            draftStale.length +
            (drafts && drafts.ttl_days
              ? ' (установленный черновик живёт ' + drafts.ttl_days + ' дн.)' : ''),
            'pl-1 text-[0.625rem] leading-snug text-(--ui-text-tertiary)'
          )),
          draftOthers.length
            ? jsx('div', {
                className: 'flex flex-col gap-0.5',
                children: draftOthers.slice(0, 5).map((d) => jsxs('div', {
                  key: d.key,
                  className: 'flex min-w-0 items-center gap-1',
                  children: [
                    jsx('span', Ell(
                      d.key + ' · ' + d.files + ' файл(ов)' +
                      (d.installed ? ' · установлен' : '') +
                      (d.src ? ' · ' + d.src.slice(-40) : ''),
                      'min-w-0 truncate pl-1 text-[0.625rem] leading-snug text-(--ui-text-tertiary)'
                    )),
                    jsx(Button, {
                      size: 'icon-xs',
                      variant: 'ghost',
                      disabled: !!busy,
                      title: 'убрать рабочий каталог ' + d.key + ' (' + d.files + ' файлов) и его сырьё' +
                        (d.installed ? ': скилл в профиле не тронем' : ''),
                      'aria-label': 'убрать рабочий каталог ' + d.key,
                      onClick: () => runDrop(d.key),
                      children: jsx('span', {
                        'aria-hidden': true,
                        style: { fontSize: 13, lineHeight: 1, display: 'block' },
                        children: '🗑'
                      })
                    })
                  ]
                }))
              })
            : null,
          draftStale.length
            ? jsx('div', {
                className: 'flex min-w-0 rounded',
                style: { backgroundColor: REVIEW_BG, alignSelf: 'flex-start', maxWidth: '100%' },
                children: jsxs(Button, {
                  size: 'sm',
                  variant: 'ghost',
                  disabled: !!busy,
                  onClick: runPrune,
                  title: TIP.prune,
                  className: 'h-7 justify-start text-xs text-(--ui-text-primary)',
                  style: BTN_FIT,
                  children: [
                    jsx('span', { 'aria-hidden': true, style: { flexShrink: 0 }, children: '🧹' }),
                    cutSpan('Убрать лишнее (' + draftStale.length + ')', undefined, TIP.prune)
                  ]
                })
              })
            : null
        ]
      })
    : null

  /* --- переходы «ДАЛЕЕ» -------------------------------------------------------
     Проверка дешёвая и на месте: не пускаем дальше, когда дальше нечего делать,
     и говорим причину подписью у кнопки (hint), а не молчанием. Никакого REST в
     самих проверках — только состояние панели. */
  const hasDraft = !!(draft && draft.has_draft)
  /* Готовность - отдельный признак от «каталог найден»: её объявляет сам LLM
     маркером READY.json. Пока маркера нет, черновик ПИШЕТСЯ, и всё, что пишет в
     профиль, заперто - иначе в скилл уедет обрывок. */
  const draftReady = !!(draft && draft.ready)
  const draftWriting = !!(draft && draft.has_draft && !draft.ready)
  /* Идёт ли работа LLM прямо сейчас: либо агент занят в сессии, куда ушло задание
     (сигнал хоста - тот же пульс, что в статусбаре), либо черновик начат и маркера
     готовности ещё нет. Плашка под заголовком показывает это жёлтым: зелёное
     «прямой режим» в эти минуты - неправда. */
  const llmTurn = !!(draftWriting || (llmSid && busyMap && busyMap[llmSid]))

  /* Шаг 1 → 2 (или сразу 3, если источник — готовый markdown). Разбор запускаем
     заодно: «ДАЛЕЕ» значит «идём дальше», а не «вернись и нажми ещё раз». */
  const next1 = async () => {
    dropPreview()   // переход по мастеру обесценивает собранный план записи
    if (!trimSrc) {
      setTone('error')
      setStatus('блок 1 · пустой источник: вставь URL или путь к файлу')
      return
    }
    /* Имени здесь больше нет: категория и имя скилла - реквизиты записи (блок 3),
       а не вход разбора. Кнопка установки гаснет без имени там, где имя нужно. */
    if (mdSrc) {
      onlyB(3)
      if (!analyzed) {
        const res = await runRerun()
        /* Сказали «markdown», а пришёл HTML — или ядро упало. Не тащим это в
           черновик молча: открываем блок 2 с честной причиной. */
        if (!res.ok) { onlyB(2); return }
        if (!res.md) {
          onlyB(2)
          setStatus('блок 1 · источник не похож на markdown (стратегия ' + (res.strategy || '?') + ') - смотри блок 2')
        }
      }
      return
    }
    onlyB(2)
    if (!analyzed) await runRerun()
  }

  /* Шаг 2 → 3: реквизиты записи. Отчёт о разборе - вход для них: по нему панель
     предлагает ИМЯ (заголовок страницы), а по имени ядро отыскивает тот же скилл в
     категории, чтобы предложить «долив» или «замену». */
  const next2 = () => {
    if (!analyzed) {
      setTone('error')
      setStatus('блок 2 · сначала разбери источник - без отчёта не собрать реквизиты записи')
      return
    }
    onlyB(3)
  }

  /* Шаг 3 → 4: без имени скилла черновику некуда ложиться (каталог установки
     называется именем), поэтому пустое имя дальше не пускает - и говорит причину. */
  const next3 = () => {
    /* Блоки открываются кликом по шапке, поэтому одной блокировки кнопки мало: тот же
       запрет проверяем и здесь - иначе «ДАЛЕЕ» можно нажать в обход блока 2. */
    if (!block2Passed) {
      setTone('error')
      setStatus('блок 3 · сначала пройди блок 2 - «Анализ источника»: отчёт о разборе даёт имя скилла и метрики')
      return
    }
    if (nameWarn) {
      setTone('error')
      setStatus('блок 3 · без имени скилла нельзя: каталог установки называется именем')
      return
    }
    if (nameBad) {
      setTone('error')
      setStatus('блок 3 · такое имя Hermes не примет: только строчные латинские буквы, цифры, «-», «_», «.» (заглавные и пробелы валят шапку скилла)')
      return
    }
    dropPreview()   // реквизиты могли поменяться - прежний план записи устарел
    onlyB(4)
  }

  /* Шаг 4 → 5: записывать в профиль можно только ГОТОВЫЙ черновик.
       Каталога в staging мало: пока LLM не положила маркер готовности, там обрывок. */
    const next4 = () => {
      if (!block2Passed) {
        setTone('error')
        setStatus('блок 4 · сначала пройди блок 2 - «Анализ источника»: без разбора черновик соберётся вслепую')
        return
      }
      if (!hasDraft) {
        setTone('error')
        setStatus('блок 4 · черновика в staging нет - нажми «Сделать черновик»')
        return
      }
      if (!draftReady) {
        setTone('working')
        const c = (draft && draft.counts) || {}
        setStatus('блок 4 · черновик ещё пишется' +
          (c.files ? ' (' + c.files + ' ' + plural(c.files, 'файл', 'файла', 'файлов') +
            (c.chapters ? ', ' + c.chapters + ' ' + plural(c.chapters, 'глава', 'главы', 'глав') : '') + ')' : '') +
          ' - кнопка записи откроется, когда LLM допишет и положит маркер готовности')
        return
      }
      setReadySeen(true)
      onlyB(5)
    }

  return jsxs('div', {
    ref: bodyRef,
    className: cn('flex h-full min-h-0 flex-col gap-2 overflow-y-auto p-3'),
    style: FIELD_CHROME,
    children: [
      /* Шапка — КОЛОНКА, а не ряд: плашка режима всегда стоит второй строкой под
         названием плагина (решение владельца: «просто смести плашку вниз, под
         название плагина — в этом проекте это допустимо»). В ряду она блуждала:
         при сжатии панели «BOOK → SKILL» переносился по пробелам, ряд вырастал в
         2–3 строки (замер: rowH 32/48 вместо 16) и плашка уезжала на 9–17 px вниз.
         В колонке её позиция не зависит от ширины вовсе. */
      jsxs('div', {
        className: 'flex w-full items-start justify-between gap-2',
        children: [
          jsxs('div', {
            className: 'flex min-w-0 flex-col items-start gap-1',
            children: [
              jsxs('div', {
                className: 'flex items-center gap-2 min-w-0',
                children: [
                  jsx(StatusDot, {
                    tone: dotTone,
                    /* SDK рисует 'good' как `bg-primary` - это АКЦЕНТ ТЕМЫ, а не зелёный:
                       на светлой теме точка «ядро на связи» выглядела белой (владелец:
                       «почему шарик белый, а при ошибке красный»). Красный работает,
                       потому что 'bad' = `bg-destructive`. Зелёный задаём свой - тем же
                       STEP_GREEN, что у состояния шага; остальные тона SDK красит верно. */
                    style: Object.assign({ flexShrink: 0 },
                      dotTone === 'good' ? { backgroundColor: STEP_GREEN } : {}),
                  }),
                  jsx('span', Ell('BOOK → SKILL', 'text-xs font-medium text-(--ui-text-primary)'))
                ]
              }),
              /* Работа LLM - ПЕРВОЙ веткой: в эти минуты панель не в прямом режиме, и
                 зелёная плашка «ядро на связи» читалась как «всё готово». Владелец:
                 «пиши в плашке просто "Работает LLM..." и всё» - задача в подписи
                 сбивала с толку: работа идёт не обязательно над черновиком. Тултип
                 называет цену: счёт растёт в чате. */
              llmTurn
                ? jsx(Badge, {
                    variant: 'warn', style: BTN_FIT,
                    title: 'Агент работает в чате - платный шаг: счёт растёт с каждой итерацией. Панель ждёт результат и заперла кнопки этого шага',
                    children: cutSpan('Работает LLM...')
                  })
                : isWorking
                  ? jsx(Badge, { variant: 'warn', style: BTN_FIT, children: cutSpan('работаю') })
                  : core === null
                    ? jsx(Badge, {
                        variant: 'muted', style: BTN_FIT,
                        title: 'проверяю ответ локального ядра: GET /api/plugins/b2s/health',
                        children: cutSpan('проба ядра…')
                      })
                    : core
                      /* Зелёный success, а не серый muted: «на связи» — это норма, и она должна
                         читаться состоянием, а не фоном; подробности (staging, python, черновики) —
                         в наведении. Badge SDK тоже `shrink-0 whitespace-nowrap` в базовом классе,
                         поэтому сжимается инлайном BTN_FIT + подпись cutSpan (грабля 15). */
                      ? jsx(Badge, {
                          variant: 'success', style: BTN_FIT, title: coreTip,
                          children: cutSpan('прямой режим · локальный Python')
                        })
                      : jsx(Badge, {
                          variant: 'warn', style: BTN_FIT,
                          title: 'Маршруты /api/plugins/b2s/ ещё не смонтированы - панель уходит в чат. Перезапусти dashboard-службу: tools/restart_dashboard.py',
                          children: cutSpan('ядро не ответило - перезапусти dashboard')
                        })
            ]
          }),
          /* Урна - в правом верхнем углу шапки: убрать промежуточное разом, не
             дожидаясь TTL и лимитов ядра. Скиллы в профиле не трогаются, а панель
             после уборки сбрасывает поля: остаются источник и категория скилла. */
          jsx(Button, {
            size: 'sm',
            variant: 'ghost',
            disabled: !!busy,
            onClick: runPurgeAll,
            className: 'h-6 shrink-0 justify-end text-[0.625rem] text-(--ui-text-primary)',
            style: CHIP_FIT,
            /* Подсказка едет через подпись (``fitLabel`` → ``Ell`` ставит title на
               span): SDK-кнопка свой ``title`` в DOM не отдаёт - тултип пропадал. */
            children: fitLabel('🗑️', TIP.purgeAll)
          })
        ]
      }),
      /* Назначение инструмента — одной строкой с многоточием: при сужении панели
         подпись не растягивается в несколько строк, полный текст — в title (наведение). */
      jsx('div', Ell('Инструмент создания скиллов из документации (html, pdf и других)',
        'text-[0.625rem] leading-snug text-(--ui-text-tertiary)')),

      /* Строка статуса живёт ВНЕ блоков: её пишут все шаги, и внутри одного блока
         она сообщала бы «ничего не происходит» всем остальным. */
      statusLine,

      /* ── 1. Источник ──────────────────────────────────────────────────────
         Категория и имя скилла отсюда УЕХАЛИ в блок 3: это реквизиты ЗАПИСИ, и
         спрашивать их на шаге 1 значило спрашивать про то, чего на этом шаге ещё
         нет. Здесь остаётся один вход - источник. */
      jsx(PaneBlock, {
        n: 1,
        title: 'Источник',
        /* Состояние источника меняется ПО ФАКТУ разбора, а не только по виду входа:
           «ДАЛЕЕ» блока 1 запускает анализ, и пока он шёл, заголовок всё ещё говорил
           «нужен разбор» (владелец: «анализ завершён - тогда и менять состояние»). */
        state: !trimSrc
          ? 'пусто'
          : (mdSrc
            ? 'markdown: блок 2 пропустим'
            : (busy === 'rerun'
              ? (isRemoteSrc(trimSrc) ? 'URL - разбираю…' : 'файл - разбираю…')
              : (analyzed
                ? (isRemoteSrc(trimSrc) ? 'URL - анализ завершён' : 'файл - анализ завершён')
                : (isRemoteSrc(trimSrc) ? 'URL - нужен разбор' : 'файл - нужен разбор')))),
        tone: trimSrc
          ? (mdSrc || analyzed ? 'done' : (busy === 'rerun' ? 'working' : (rerunErr ? 'bad' : null)))
          : 'bad',
        open: !!openB[1],
        onToggle: () => toggleB(1),
        style: { backgroundColor: openB[1] ? BLOCK_BG : 'transparent' },
        hint: !trimSrc
          ? 'впиши URL или путь к файлу'
          : (mdSrc
            ? 'файл уже markdown - анализ пропустим'
            : (busy === 'rerun'
              ? 'иду разбор источника: загрузка, очистка, метрики'
              : (analyzed
                ? 'источник разобран - можно делать черновик'
                : (rerunErr ? 'разбор не прошёл: ' + rerunErr : 'блок 2 разберёт источник')))),
        foot: jsx(NextBtn, {
          label: 'ДАЛЕЕ →',
          onClick: next1,
          disabled: !!busy || !trimSrc,
          fill: STEP_BG,
          title: !trimSrc
            ? 'сначала впиши источник'
            : 'запомнить выбор и открыть следующий шаг'
        }),
        children: [
          jsx(Field, {
            label: 'Источник',
            hint: 'URL или путь',
            children: jsxs('div', {
              ref: srcRow,
              className: 'flex items-center gap-1',
              children: [
                jsx(Input, {
                  value: src,
                  onChange: (e) => setSrc(e.target.value),
                  placeholder: 'https://… или D:/путь/файл.md',
                  className: 'h-7 text-xs',
                  style: { minWidth: 0, flex: '1 1 auto' }
                }),
                /* Пиктограммы вместо подписей: «Выбрать файл» и «Вставить из буфера
                   обмена» в узкую панель не влезают, а сжатые превращаются в кашу.
                   Полный текст — в нативном тултипе (title) и для озвучки (aria-label). */
                jsx(Button, {
                  variant: 'secondary',
                  size: 'icon-xs',
                  className: 'shrink-0',
                  title: 'Выбрать файл на диске - полный путь встанет в поле «Источник»',
                  'aria-label': 'Выбрать файл',
                  onClick: pickFile,
                  children: iconFolder()
                }),
                jsx(Button, {
                  variant: 'secondary',
                  size: 'icon-xs',
                  className: 'shrink-0',
                  title: 'Вставить из буфера обмена - URL страницы или путь к файлу',
                  'aria-label': 'Вставить из буфера обмена',
                  onClick: pasteSrc,
                  children: iconClipboard()
                })
              ]
            })
          }),


          jsx(Field, {
            label: 'Стратегия загрузки',
            children: jsxs(Select, {
              value: strat,
              onValueChange: setStrat,
              children: [
                jsx(SelectTrigger, { className: 'h-7 text-xs', children: vFit(jsx(SelectValue, {}), labelOf(STRATEGIES, strat)) }),
                jsx(SelectContent, {
                  children: STRATEGIES.map((s) => jsx(SelectItem, { value: s.value, children: s.label }, s.value))
                })
              ]
            })
          }),

          jsxs('div', {
            className: 'grid grid-cols-2 gap-2',
            children: [
              jsx(Field, {
                label: 'Режим',
                children: jsxs(Select, {
                  value: mode,
                  onValueChange: setMode,
                  children: [
                    jsx(SelectTrigger, { className: 'h-7 text-xs', children: vFit(jsx(SelectValue, {}), labelOf(MODES, mode)) }),
                    jsx(SelectContent, {
                      children: MODES.map((m) => jsx(SelectItem, { value: m.value, children: m.label }, m.value))
                    })
                  ]
                })
              }),
              jsx(Field, {
                label: 'Язык скилла',
                children: jsxs(Select, {
                  value: lang,
                  onValueChange: setLang,
                  children: [
                    jsx(SelectTrigger, { className: 'h-7 text-xs', children: vFit(jsx(SelectValue, {}), labelOf(LANGS, lang)) }),
                    jsx(SelectContent, {
                      children: LANGS.map((l) => jsx(SelectItem, { value: l.value, children: l.label }, l.value))
                    })
                  ]
                })
              })
            ]
          })
        ]
      }),

      /* ── 2. Анализ источника (для готового .md — пропускается) ──────────── */
      jsx(PaneBlock, {
        n: 2,
        title: mdSrc ? 'Анализ MD файла' : 'Анализ источника',
        state: mdSrc
          ? 'пропущен: файл уже markdown'
          : (busy === 'rerun'
            ? 'разбираю прямо сейчас'
            : (analyzed
              ? 'готов - ' + (report && report.chars ? fmtInt(report.chars) + ' симв.' : 'отчёт есть')
              : (staleReport ? 'отчёт от прежних входов - прогони снова' : (rerunErr ? 'сорвался' : 'ещё не запускался')))),
        tone: mdSrc ? 'skip' : (busy === 'rerun' ? null : (analyzed ? 'done' : (rerunErr ? 'bad' : null))),
        open: !!openB[2],
        onToggle: () => toggleB(2),
        style: { backgroundColor: openB[2] ? BLOCK_BG : 'transparent' },
        hint: mdSrc
          ? 'markdown - уже текст: чистить нечего, метрики посмотреть можно'
          : (busy === 'rerun'
            ? 'ядро читает источник прямо сейчас - мимо чата'
            : (analyzed ? 'источник разобран - можно к черновику'
              : (staleReport ? 'отчёт ниже - от прежних входов, а не от этих' : 'скачать и вычистить текст: мимо чата, прямо в ядро'))),
        foot: jsx(NextBtn, {
          label: 'ДАЛЕЕ →',
          onClick: next2,
          disabled: !!busy || !analyzed,
          fill: STEP_BG,
          title: analyzed ? 'открыть черновик'
            : (staleReport ? 'входы изменились - прогони разбор заново' : 'сначала прогони анализ источника')
        }),
        children: [
          mdSrc
            ? jsx('div', Ell('⚠ «уже markdown» решено по имени файла, до сети. Если это не так - жми «Прогнать всё равно», ядро разберёт как страницу.',
                'text-[0.625rem] leading-snug opacity-80'))
            : null,
          jsx('div', {
            className: 'flex min-w-0 flex-wrap items-center gap-1',
            children: [
              jsx('div', {
                className: 'flex min-w-0 rounded',
                style: { backgroundColor: STEP_BG },
                children: jsxs(Button, {
                  size: 'sm',
                  variant: 'ghost',
                  disabled: !!busy,
                  onClick: runRerun,
                  title: mdSrc ? TIP.srcAnyway
                    : (busy === 'rerun' ? 'ядро читает источник прямо сейчас - это python, мимо чата и без расхода LLM'
                      : (analyzed ? TIP.srcRerun : TIP.srcAnalyze)),
                  className: 'h-7 justify-start text-xs text-(--ui-text-primary)',
                  style: BTN_FIT,
                  children: [
                    jsx('span', { 'aria-hidden': true, style: { flexShrink: 0 }, children: busy === 'rerun' ? '⏳' : '🔎' }),
                    cutSpan(mdSrc ? 'Прогнать всё равно'
                      : (busy === 'rerun' ? 'Идёт разбор…'
                        : (analyzed ? 'Прогнать заново' : 'Анализ источника и очистка')),
                      undefined,
                      mdSrc ? TIP.srcAnyway
                        : (busy === 'rerun' ? 'ядро читает источник прямо сейчас'
                          : (analyzed ? TIP.srcRerun : TIP.srcAnalyze)))
                  ]
                })
              }),
              rerunErr
                ? jsx(Button, {
                    size: 'sm',
                    variant: 'ghost',
                    disabled: !!busy,
                    onClick: runRerun,
                    className: 'h-7 text-[0.625rem]',
                    style: Object.assign({ backgroundColor: REVIEW_BG }, BTN_FIT),
                    children: cutSpan('⟳ Повторить', undefined, TIP.retry)
                  })
                : null
            ]
          }),
          rerunErr
            ? jsx('div', Ell('прогон источника сорвался: ' + rerunErr + ' - в чат это не ушло, разбор делает ядро',
                'text-[0.625rem] leading-snug text-(--ui-text-primary)'))
            : null,
          resultBlock
        ]
      }),

      /* ── 3. Категория и имя скилла (реквизиты записи — ДО черновика) ──────
         Владелец: «текущий шаг 3 сдвинется на 4 позицию и этот шаг теперь будет
         твёрдо знать правильное Имя скилла и план - доливать в текущий скилл или
         заменить». Имя и категорию спрашиваем ЗДЕСЬ, а не на записи: черновик
         обязан ложиться в известный каталог, а долив/замена считаются по имени. */
      jsx(PaneBlock, {
        n: 3,
        title: 'Категория и имя скилла',
        state: nameWarn
          ? 'имя не задано'
          : (wanted + (existing ? ' · в категории уже есть' : ' · новый')),
        tone: nameWarn ? 'bad' : null,
        open: !!openB[3],
        onToggle: () => toggleB(3),
        style: { backgroundColor: openB[3] ? BLOCK_BG : 'transparent' },
        hint: nameWarn
          ? 'имя обязательно: пустым дальше не пойдём - каталог установки называется именем'
          : (existing
              ? 'имя занято: блок 5 соберёт план ДОЛИВА, замена - только твой явный выбор'
              : 'имя свободно: блок 4 соберёт новый скилл'),
        foot: jsx(NextBtn, {
          label: 'ДАЛЕЕ →',
          onClick: next3,
          disabled: !!busy || nameWarn || nameBad || !block2Passed,
          fill: STEP_BG,
          title: !block2Passed
            ? 'сначала пройди блок 2 - «Анализ источника»: отчёт о разборе даёт имя скилла и его метрики'
            : (nameWarn
              ? 'нужно имя скилла: пустым не поставим - каталог в skills/ должен быть назван'
              : (nameBad
                ? 'такое имя Hermes не примет: нужны строчные латинские буквы, цифры, «-», «_», «.»'
                : 'открыть черновик - он ляжет в ' + (cat ? cat + '/' : '') + wanted))
        }),
        children: [
          jsx('span', Ell('Это реквизиты ЗАПИСИ, а не входы разбора. Панель спрашивает их до черновика: блок 4 (черновик) получает готовое имя и категорию, а план записи в блоке 5 сразу считается как долив или замена.',
            'text-[0.625rem] leading-snug text-(--ui-text-tertiary)')),
          jsxs('div', {
            className: 'grid grid-cols-2 gap-2',
            children: [
              jsx(Field, {
                label: 'Категория скилла',
                children: cats && cats.length
                  ? jsxs('div', { className: 'space-y-1', children: [
                      jsxs(Select, {
                        value: isCustomCat ? '__custom__' : cat,
                        onValueChange: (v) => setCat(v === '__custom__' ? '' : v),
                        /* Список читается заново на каждое открытие — выпадашка не
                           может показывать профиль вчерашней давности. */
                        onOpenChange: (open) => { if (open) loadCats() },
                        children: [
                          jsx(SelectTrigger, { className: 'h-7 text-xs', children: vFit(jsx(SelectValue, {}), isCustomCat ? 'своя категория' : cat) }),
                          jsx(SelectContent, {
                            children: [
                              ...cats.map((c) => jsx(SelectItem, {
                                value: c,
                                children: (catMeta && catMeta[c] && catMeta[c].empty) ? c + ' (пусто)' : c
                              }, c)),
                              /* Выход из списка: своя категория. Без него выбор был
                                 клеткой — подходящей категории в профиле нет, и скилл
                                 уезжал в чужую, лишь бы из списка. */
                              jsx(SelectItem, { value: '__custom__', children: '✎ своя категория…' })
                            ]
                          })
                        ]
                      }),
                      isCustomCat
                        ? jsxs('div', { className: 'space-y-1', children: [
                            jsx(Input, {
                              value: cat,
                              onChange: (e) => setCat(e.target.value),
                              placeholder: 'имя новой категории: латиница и дефис, например mlops/tuning',
                              className: 'h-7 text-xs'
                            }),
                            jsxs('div', { className: CHIP_BOX, style: { backgroundColor: CHIP_BG }, children: [
                              jsx('div', { className: GROUP_LEAD, style: GROUP_CAP, children: [
                                jsx('div', { className: CHIP_CHIEF, children: '⚠ категории «' + (cat || '…') + '» в профиле нет - папка создастся при установке.' }),
                                jsx('div', { className: CHIP_MUTED, children: 'Опиши её здесь: без описания Hermes покажет категорию агенту голым именем, и скилл в ней будет труднее найти.' })
                              ] }),
                              jsx('div', { className: 'pt-1', children: jsx(Input, {
                                value: catDesc,
                                onChange: (e) => setCatDesc(e.target.value),
                                placeholder: 'описание категории для Hermes - одной строкой',
                                className: 'h-7 text-xs'
                              }) })
                            ] })
                          ] })
                        : catChip,
                      /* Строку про «скиллы вне категорий» больше не показываем: она пугала
                         зря — пять таких каталогов Hermes сам считает категориями (и они
                         вернулись в выпадашку), а настоящий сирота — SKILL.md прямо в
                         skills/ — случай служебный и внимания владельца не стоит.
                         Данные остаются в ответе ядра как catLoose. */
                    ] })
                  : jsx(Input, {
                      value: cat,
                      onChange: (e) => setCat(e.target.value),
                      placeholder: cats === null ? 'список грузится…' : 'списка нет - перезапусти dashboard',
                      title: catErr,
                      className: 'h-7 text-xs'
                    })
              }),
              jsxs(Field, {
                label: 'Имя скилла',
                hint: nameWarn ? 'обязательное: пустым не поставим' : (existing ? 'занято - долив' : skills ? 'свободно - новый' : ''),
                children: [
                  jsx(Input, {
                    value: name,
                    onChange: (e) => { setName(e.target.value); nameTouched.current = true; setNameAuto(false) },
                    placeholder: 'каталог в skills/: латиница и дефисы',
                    title: skillsErr || '',
                    /* Пустое имя - не «просто пустое поле», а причина, по которой кнопка
                       «ДАЛЕЕ» не работает: окантовка называет это раньше, чем клик.
                       Рамку красим КРАСНОЙ и такой же тонкой, как у блока 3 (`color-mix`
                       45%): Сюда попадают ОБА случая - имя пустое (`nameWarn`) и имя
                       недопустимое (`nameBad`): это незаполненный обязательный вход, а не
                       авария. Цвет ПОДПИСИ под полем не трогаем - владелец просил оставить
                       текст оранжевым («пусть останется оранжевым!»). */
                    className: 'h-7 text-xs',
                    style: (nameWarn || nameBad)
                      ? { border: '1px solid color-mix(in srgb, ' + STOP_RED + ' 45%, transparent)' }
                      : undefined
                  }),
                  /* Подсказка имён — свой список ТОЛЬКО по выбранной категории
                     (см. комментарий у `nameOpen`): нативный datalist подсовывал
                     весь профиль и не давал прокрутки. */
                  namePickRow,
                  nameOpen ? nameListBlock : null,
                  jsx('div', {
                    className: 'truncate text-[10px] leading-tight' +
                      ((nameWarn || nameBad) ? '' : ' text-(--ui-text-tertiary, #8a8a8a)'),
                    style: (nameWarn || nameBad) ? { color: WARN_YELLOW } : null,
                    title: nameNote,
                    children: nameNote
                  }),
                  existing
                    ? jsxs(Select, {
                        value: act,
                        onValueChange: setAct,
                        children: [
                          jsx(SelectTrigger, { className: 'h-6 text-[10px]', children: vFit(jsx(SelectValue, {}), labelOf(ACTS, act)) }),
                          jsx(SelectContent, {
                            children: ACTS.map((a) => jsx(SelectItem, { value: a.value, children: a.label }, a.value))
                          })
                        ]
                      })
                    : null,
                  existing && act === 'replace'
                    ? jsx('div', Ell(
                        '⚠ каталог скилла будет снесён и залит заново. Перед записью ядро снимет копию в backups/, но подтверждение спрошу ещё раз.',
                        'text-[10px] leading-tight text-(--ui-text-primary)'
                      ))
                    : null
                ]
              })
            ]
          })
        ]
      }),

      /* ── 4. Черновик (генерация в staging, в постоянные скиллы не пишем) ── */
      jsx(PaneBlock, {
        n: 4,
        title: 'Черновик скилла - без записи',
        state: draftReady
          ? 'готов - в staging'
          : (draftWriting
              ? '⏳ пишется - в профиль не пойдёт'
              : (draftWait ? 'задание в чате - жду staging' : (busy === 'draft' ? 'пишется' : 'нет'))),
        /* Готовность - единственное состояние, которое красит блок зелёным:
           «каталог найден» ещё не значит «черновик написан». */
        tone: draftReady ? 'done' : (draftWriting ? 'working' : null),
        open: !!openB[4],
        onToggle: () => toggleB(4),
        style: { backgroundColor: openB[4] ? BLOCK_BG : 'transparent' },
        hint: draftReady
          ? 'черновик готов - можно записывать в профиль'
          : (draftWriting
              ? 'черновик ещё пишется: кнопка записи откроется, когда LLM положит маркер готовности'
              : (draftWait
                  ? 'задание ушло в чат - файлы приедут в staging сами, панель следит'
                  : (mdSrc
                      ? 'блок 2 пропущен: источник уже markdown - жми «✎ Сделать черновик»'
                      : 'черновика нет: жми «✎ Сделать черновик» - прозу пишет LLM в чате'))),
        foot: jsx(NextBtn, {
          label: 'ДАЛЕЕ →',
          onClick: next4,
          /* Заперто на ГОТОВНОСТИ, а не на «каталог найден»: иначе прыткий
             пользователь уйдёт в блок 5 на середине и запишет обрывок. */
          disabled: !!busy || !draftReady || !block2Passed,
          fill: draftReady ? STEP_BG : undefined,
          title: !block2Passed
            ? 'сначала пройди блок 2 - «Анализ источника»: без разбора черновик соберётся по пустым метрикам'
            : (draftReady
              ? 'открыть запись в профиль - имя и категорию собрали в блоке 3'
              : (draftWriting
                  ? 'черновик ещё пишется - дождись маркера готовности от LLM'
                  : 'сначала сделай черновик кнопкой «✎» выше'))
        }),
        children: [
          jsx('div', {
            className: 'flex min-w-0 flex-wrap items-center gap-1',
            children: [
              jsx('div', {
                className: 'flex min-w-0 rounded',
                style: { backgroundColor: STEP_BG },
                children: jsxs(Button, {
                  size: 'sm',
                  variant: 'ghost',
                  /* Пока идёт генерация, второй запуск запрещён: две прозы пишутся в
                     один каталог и мешают друг другу. */
                  disabled: !!busy || draftWriting,
                  onClick: () => sendIntent('draft'),
                  title: draftWriting
                    ? 'черновик ещё пишется - дождись маркера готовности'
                    : (hasDraft ? TIP.redraft : TIP.draft),
                  className: 'h-7 justify-start text-xs text-(--ui-text-primary)',
                  style: BTN_FIT,
                  children: [
                    jsx('span', { 'aria-hidden': true, style: { flexShrink: 0 }, children: '✎' }),
                    cutSpan(hasDraft ? 'Перегенерировать с учётом замечаний' : 'Сделать черновик',
                      undefined,
                      hasDraft ? TIP.redraft : TIP.draft)
                  ]
                })
              }),
              jsx(Button, {
                size: 'sm',
                variant: 'ghost',
                /* Без черновика гаснет: критиковать нечего, а задание в чат — платное.
                   Причина не молчит — она в тултипе и в подписи под рядом кнопок. */
                /* Критика - разбор готового черновика: на пишущемся нечего разбирать,
                   а задание в чат - платное. Причина не молчит: она в тултипе. */
                disabled: !!busy || !draftReady,
                onClick: () => sendIntent('review'),
                title: draftReady
                  ? TIP.review
                  : (draftWriting ? 'черновик ещё пишется: критиковать пока нечего' : TIP.reviewOff),
                /* Кегль и цвет — как у соседей по ряду («Перегенерировать», «План по
                   главам»): свой 0.625rem читался «другим центрированием», хотя
                   flex-центр совпадал (замер: 0.00 px у обоих) — мелкая строка в
                   28 px кнопке просто выглядит иначе. Один кегль в ряду — одно лицо. */
                className: 'h-7 justify-start text-xs text-(--ui-text-primary)',
                style: Object.assign({ backgroundColor: REVIEW_BG }, BTN_FIT),
                children: [
                  jsx('span', { 'aria-hidden': true, style: { flexShrink: 0 }, children: '🔍' }),
                  cutSpan('Критика и список правок', undefined, draftReady ? TIP.review : (draftWriting ? 'черновик ещё пишется: критиковать пока нечего' : TIP.reviewOff))
                ]
              })
            ]
          }),
          jsx('span', Ell(draftReady
            ? 'Правки к черновику и повторный прогон считаются заново: счёт растёт с числом итераций.'
            : (draftWriting
                ? 'Черновик ещё пишется: «Критика» и «ДАЛЕЕ» включатся, когда LLM положит маркер готовности.'
                : '«Критика» включится, когда в staging появится готовый черновик - его делает кнопка «✎» выше (она не гаснет).'),
            'pl-1 text-[0.625rem] leading-snug text-(--ui-text-tertiary)')),
          mdSrc && !hasDraft
            ? jsx('span', Ell('источник - готовый markdown: текст уже добыт шагом 1, анализ (блок 2) не нужен, черновик можно делать сразу.',
                'pl-1 text-[0.625rem] leading-snug text-(--ui-text-tertiary)'))
            : null,
          draftBlock,
          chapterSlot,
          cleanupSlot
        ]
      }),

      /* ── 5. Запись в профиль (первый клик — план, второй — запись) ──────── */
      jsx(PaneBlock, {
        n: 5,
        title: 'Запись в профиль',
        state: installed
          ? 'установлен в ' + installed.target
          : (!draftReady
              ? (draftWriting ? '⏳ черновик ещё пишется' : 'черновика нет')
              : (!readySeen ? 'пройди «ДАЛЕЕ» в блоке 4'
                : (preview
                  ? (preview.mode === 'replace' ? 'подтверди ЗАМЕНУ' : 'подтверди запись')
                  : (existing ? 'долив в ' + (existing.category || 'без категории') : 'новый скилл')))),
        tone: (preview || installed) ? 'done' : null,
        open: !!openB[5],
        onToggle: () => toggleB(5),
        style: { backgroundColor: openB[5] ? BLOCK_BG : 'transparent' },
        /* Подписи-состояния у этого блока НЕТ намеренно: обе фразы ушли в тултип
           кнопки («что будет, если нажму»), а не висят рядом с ней. Владелец:
           «выводить их подсказкой на кнопке, а не рядом с кнопкой». */
        foot: jsx(NextBtn, {
          /* Два клика - два разных слова на кнопке, а не одно и то же действие.
             До предпросмотра кнопка обещает ровно то, что сделает: показать план
             (ноль риска), и только после него — «Установить». Владелец: «до первого
             клика должна быть надпись "Предпросмотр", и только после его выполнения
             надпись меняется на "Установить"». Замена сносит каталог — у неё своя подпись. */
          label: installed
            ? 'Успешно установлен'
            : (preview
              ? (preview.mode === 'replace' ? 'Подтвердить ЗАМЕНУ' : 'Установить')
              : 'Предпросмотр'),
          onClick: () => runInstall(!!preview),
          /* Запись заперта на двух замках: черновик ГОТОВ (маркер от LLM) и человек
             прошёл «ДАЛЕЕ» в блоке 4. Так в профиль не уедет ни обрывок, ни план,
             собранный в обход блока 4. */
          disabled: !!busy || !draftReady || !readySeen || !!installed || nameWarn || nameBad || !block2Passed,
          fill: (draftReady && readySeen) ? STEP_BG : undefined,
          title: installed
            ? 'скилл уже записан в ' + installed.target + ' - чтобы поставить заново, измени черновик или входы'
            : (!block2Passed
              ? 'сначала пройди блок 2 - «Анализ источника»: запись в профиль без разбора - это вслепую'
              : (nameWarn
                ? 'нужно имя скилла: пустым не поставим - каталог в skills/ должен быть назван'
                : (nameBad
                  ? 'такое имя Hermes не примет: нужны строчные латинские буквы, цифры, «-», «_», «.» - линза валит шапку сразу после записи'
                  : (!draftReady
                    ? (draftWriting
                      ? 'черновик ещё пишется - записывать нечего, дождись маркера готовности'
                      : 'сначала сделай черновик - записывать нечего')
                    : (!readySeen
                      ? 'сначала пройди «ДАЛЕЕ» в блоке 4: запись открывается только после готового черновика'
                      : (preview
                        ? 'второй клик пишет в skills/<категория>/<имя>/'
                        : '«Предпросмотр» соберёт план и ничего не запишет - пишет только «Установить»'))))))
        }),
        children: [
          jsx('span', Ell(installed
            ? 'записан в ' + installed.target + ' - второй клик не нужен: кнопка гаснет до правки черновика или входов'
            : (!draftReady
                ? (draftWriting
                  ? 'черновик ещё пишется: ядро не пустит запись, пока LLM не положит маркер готовности (READY.json)'
                  : 'черновика нет: сначала кнопка «✎ Сделать черновик» в блоке 4')
                : (!readySeen
                  ? 'черновик готов, но блок 4 не пройден: жми «ДАЛЕЕ →» там - запись откроется после этого'
                  : (preview
                    ? (preview.mode === 'replace'
                      ? 'второй клик = снести каталог и залить черновик целиком; бэкап снимается до записи'
                      : 'второй клик = записать план в skills/<категория>/<имя>/')
                    : (existing
                      ? 'имя занято → долив: новые главы лягут рядом, старое не тронем. План соберёт «Предпросмотр»'
                      : 'перенос в skills/ - сначала «Предпросмотр» без записи, пишет только «Установить». Любое действие в блоках 1-4 сбрасывает план')))),
            'text-[0.625rem] leading-snug text-(--ui-text-tertiary)')),

          planBlock
        ]
      }),

      jsxs('div', {
        className: 'flex flex-col gap-0.5 pt-1 text-[0.625rem] text-(--ui-text-tertiary)',
        children: [
          jsx('span', Ell('REST: /rerun · /install · /plan · /skills · /categories · /text · /drafts · /mark_ready · /drop · /prune')),
          jsx('span', Ell('сессия (для чат-шагов): ' + (focusedId || '-')))
        ]
      })
    ]
  })
}

export default {
  id: 'b2s',
  name: 'BOOK → SKILL',
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'BOOK → SKILL',
      data: { placement: 'right', width: '340px' },
      render: () => jsx(B2SPane, { ctx })
    })
  }
}
