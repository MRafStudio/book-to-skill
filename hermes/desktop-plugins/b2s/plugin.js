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
import { useEffect, useState } from 'react'

const STRATEGIES = [
  { value: 'auto', label: 'auto — каскад стратегий' },
  { value: 'raw-md', label: 'raw-md — источник уже markdown' },
  { value: 'trafilatura-html', label: 'trafilatura — основная очистка' },
  { value: 'bs4-html', label: 'bs4 — добор хвоста и таблиц' },
  { value: 'stdlib-html', label: 'stdlib — крайний случай' }
]

const MODES = [
  { value: 'technical', label: 'technical — таблицы и код' },
  { value: 'text', label: 'text — проза' }
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
  { value: 'auto', label: 'долив — дополнить существующий' },
  { value: 'replace', label: 'замена — снести и залить заново (бэкап)' }
]

/** План установки из ядра (ответ /install без записи) — человеческими строками. */
function planRows(out) {
  const plan = (out && out.plan) || {}
  const rows = []
  const put = (title, list) => {
    const items = list || []
    if (items.length) {
      rows.push(title + ' (' + items.length + '): ' + items.slice(0, 5).join(', ') +
        (items.length > 5 ? ' …' : ''))
    }
  }
  put('＋ добавится', plan.added)
  put('⟳ перезапишется', plan.overwrite)
  put('＝ останется как есть', plan.keep)
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
      : '⟳ журнал: источник уже был — обновлён') +
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

function Field({ label, hint, children }) {
  return jsxs('label', {
    className: 'flex min-w-0 flex-col gap-1',
    children: [
      jsxs('span', {
        className: 'flex items-baseline gap-2',
        children: [
          jsx('span', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: label }),
          hint
            ? jsx('span', { className: 'text-[0.625rem] text-(--ui-text-tertiary) opacity-70', children: hint })
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
  if (kind === 'desc' || kind === 'desc-fix') {
    push('cat', f.cat)
    push('dmode', kind === 'desc-fix' ? 'fix' : 'write')
    push('desc', f.desc)
    return parts.join(' | ')
  }
  if (kind !== 'install' && kind !== 'review' && kind !== 'plan') push('src', f.src)
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
  const [src, setSrc] = useState(stored.src || 'https://docs.python.org/3/library/pathlib.html')
  const [name, setName] = useState(stored.name || 'python-pathlib')
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
  const [report, setReport] = useState(null)     // последний прогон из /state
  const [preview, setPreview] = useState(null)   // предпросмотр установки (без записи)
  const [chapterPlan, setChapterPlan] = useState(null) // раскладка по главам (ответ /plan)
  const [chapterBusy, setChapterBusy] = useState(false)
  const [text, setText] = useState('')           // очищенный текст источника — первый экран панели
  const [textInfo, setTextInfo] = useState(null) // путь/объём/обрезано — ответ /text
  const [textBusy, setTextBusy] = useState(false)
  const [outOpen, setOutOpen] = useState(false)  // свёртка «Результат разбора» (шаг 1)

  const focusedId = useValue(host.state.focusedSessionId)

  /* Занятое имя скилла — это не ошибка ввода, а состояние: скилл с таким именем
     уже стоит, и источник доливается в него. Отсюда и режим установки. */
  const wanted = (name || '').trim()
  const existing = (skills || []).find((s) => s.name === wanted) || null

  // Имя занято → категорию берём у самого скилла (он лежит в своей категории),
  // а «замену» сбрасываем на долив: по умолчанию ничего не сносим.
  useEffect(() => {
    if (!existing) return
    if (existing.category) setCat(existing.category)
    setAct((cur) => (cur === 'replace' ? 'replace' : 'auto'))
  }, [existing && existing.name])

  // План считался для конкретных имени, категории и режима. Поменяли что-то —
  // старый план уже не про этот случай, а «второй клик» подтвердил бы не то.
  useEffect(() => { setPreview(null); setChapterPlan(null) }, [name, cat, act])

  useEffect(() => {
    ctx.storage.set('fields', { src, name, strat, mode, lang, cat, act, catDesc })
  }, [src, name, strat, mode, lang, cat, act, catDesc])

  /** Текст источника — тот же шаг 1, но без LLM: ядро отдаёт файл из b2s_fetched.
      limit=0 — файл целиком («показать весь текст»), иначе только первый экран. */
  const loadText = async (limit = 6000) => {
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
        if (limit) setOutOpen(true)
      } else {
        setTextInfo((prev) => ({ ...(prev || {}), error: (out && out.error) || 'текст недоступен' }))
      }
    } catch (err) {
      setTextInfo((prev) => ({ ...(prev || {}), error: note(err) }))
    } finally {
      setTextBusy(false)
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
        try {
          const s = await ctx.rest('/state', { timeoutMs: 8000 })
          const last = s && s.history && s.history.length ? s.history[0] : null
          if (alive && last) {
            setReport(last)
            loadText(6000)   // текст последнего прогона виден сразу, без кликов
          }
        } catch (err) { /* история не критична */ }
        setStatus('ядро на связи — шаги 1 и 3 идут мимо чата')
      } catch (err) {
        if (!alive) return
        setCore(false)
        setStatus('ядро не ответило: маршруты /api/plugins/b2s/ ещё не смонтированы — перезапусти dashboard-службу')
      }
    }
    load()
    return () => { alive = false }
  }, [])

  // Категории. По категории Hermes решает, когда подгружать скилл, поэтому выбор
  // идёт из существующих — но список не клетка: «своя категория…» заводит новую,
  // и это осознанный шаг (каталог создастся при установке, описание пишем сразу).
  // К каждой категории ядро отдаёт её описание — чипса показывает суть, а не
  // служебный текст, и предупреждает, когда Hermes описания не увидит.
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const out = await ctx.rest('/categories', { timeoutMs: 8000 })
        if (!alive) return
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
        if (!alive) return
        setCats([])
        setCatErr('список категорий недоступен: ' + note(err))
      }
    }
    load()
    return () => { alive = false }
  }, [])

  // Существующие скиллы профиля: имя выбирают из списка, а не придумывают.
  // Занятое имя — не отказ, а сигнал «источник доливается в этот скилл»: панель
  // сама переключается в режим дополнения. Иначе на 600 страницах пришлось бы
  // каждый раз угадывать правильное имя, а промах создал бы второй скилл-дубль.
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const out = await ctx.rest('/skills', { timeoutMs: 10000 })
        if (!alive) return
        setSkills(Array.isArray(out && out.skills) ? out.skills : [])
        setSkillsErr('')
      } catch (err) {
        if (!alive) return
        setSkills([])
        setSkillsErr('список скиллов недоступен: ' + note(err))
      }
    }
    load()
    return () => { alive = false }
  }, [])

  const sendIntent = async (kind) => {
    const sid = host.state.focusedSessionId.get()
    if (!sid) {
      setTone('error')
      setStatus('нет активной сессии — открой чат и повтори')
      return false
    }
    const intentText = intentOf(kind, { src, name, strat, mode, lang, cat, act, desc: catDesc })
    setBusy(kind)
    setOutOpen(true)
    try {
      await host.request('prompt.submit', { session_id: sid, text: intentText })
      setTone('sent')
      setStatus('→ ушло в чат агенту: ' + intentText)
      return true
    } catch (err) {
      setTone('error')
      setStatus('не доехало: ' + (err && err.message ? err.message : String(err)))
      return false
    } finally {
      setBusy('')
    }
  }

  const note = (err) => (err && err.message ? err.message : String(err))

  /** Шаг 1: детерминированный прогон источника. Никакого чата — прямой REST. */
  const runRerun = async () => {
    setBusy('rerun')
    setTone('working')
    setOutOpen(true)
    setStatus('шаг 1 · загрузка и очистка источника…')
    try {
      const out = await ctx.rest('/rerun', {
        method: 'POST',
        body: { src, strat, mode, name, cat },
        timeoutMs: 300000
      })
      setCore(true)
      if (out.report) setReport({ ...out.report, won: out.strategy, at: 'сейчас' })
      if (out.fetch_ok) {
        setTone('done')
        setStatus('источник разобран: ' + summaryOf(out))
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
          setOutOpen(true)
        }
        loadText(6000)   // уточняем из файла: весь объём, число строк. Нет маршрута — останется превью
      } else {
        setTone('error')
        setStatus('источник не разобран: ' + (out.warning || 'причина неизвестна'))
      }
    } catch (err) {
      setCore(false)
      setTone('error')
      setStatus('REST-ядро недоступно (' + note(err) + ') — ухожу в чат')
      await sendIntent('rerun')
    } finally {
      setBusy('')
    }
  }

  /* Шаг 3: предпросмотр переноса — тоже без LLM. Запись только по второму клику.
     Режим (долив/замена) едет в ядро явно: там он превращается в план
     «добавится / перезапишется / останется», и только увидев этот план,
     второй клик имеет право писать. Замена сносит каталог — поэтому при
     ней ядро сперва снимает бэкап, а панель показывает предупреждение. */
  const runInstall = async (confirm) => {
    setBusy('install')
    setTone('working')
    setOutOpen(true)
    setStatus(confirm ? 'установка · перенос в skills/…' : 'шаг 3 · предпросмотр переноса…')
    try {
      const out = await ctx.rest('/install', {
        method: 'POST',
        body: { name, cat, confirm, mode: act, allow_overwrite: true, cat_desc: catDesc },
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
          setStatus('в staging/' + name + ' нет SKILL.md — сначала черновик')
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
      } else {
        setTone('error')
        setStatus('записано, но проверка не прошла: ' + (out.error || 'см. подробности ниже'))
      }
    } catch (err) {
      setTone('error')
      setStatus('REST-ядро недоступно (' + note(err) + '): установка не выполнена')
    } finally {
      setBusy('')
    }
  }

  /* План долива по главам. Файловый план (planBlock) говорит, ЧТО ляжет, но не
     отвечает, что слить со старыми главами, а что писать заново — для этого
     нужна близость тем, её считает Python. Запись в скилл тут невозможна:
     план кладётся рядом с черновиком, агент читает его файлом. */
  const runPlan = async () => {
    setChapterBusy(true)
    setTone('working')
    setOutOpen(true)
    setStatus('шаг 3 · раскладка по главам…')
    try {
      const out = await ctx.rest('/plan', {
        method: 'POST',
        body: { name, cat, mode: act, save: true },
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
        setStatus('скилла «' + name + '» ещё нет — все ' + ((out.chapters || []).length) +
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
  const dotTone = tone === 'error' ? 'bad' : core === false ? 'warn' : isWorking || tone === 'done' ? 'good' : 'muted'

  const steps = [
    {
      kind: 'rerun',
      icon: '🔎',
      title: 'Анализ источника и очистка',
      note: 'скачать и вычистить текст — мимо чата, прямо в ядро; файлы скилла не пишутся',
      run: runRerun
    },
    {
      kind: 'draft',
      icon: '✎',
      title: 'Сделать черновик',
      note: 'проза глав — это работа LLM, поэтому идёт в чат (staging/, перегенерация после правок)',
      run: () => sendIntent('draft')
    },
    {
      kind: 'install',
      icon: '⇩',
      title: preview
        ? (preview.mode === 'replace' ? 'Подтвердить ЗАМЕНУ' : preview.mode === 'append' ? 'Подтвердить долив' : 'Подтвердить установку')
        : existing
          ? (act === 'replace' ? 'Заменить скилл' : 'Дополнить скилл')
          : 'Установить',
      note: preview
        ? (preview.mode === 'replace'
            ? 'второй клик = снести каталог и залить черновик целиком; бэкап снимается до записи'
            : 'второй клик = записать план в skills/<категория>/<имя>/')
        : existing
          ? 'имя занято → долив: новые главы лягут рядом, старое не тронем'
          : 'перенос в skills/ — сначала предпросмотр без записи, пишет только второй клик',
      run: () => runInstall(!!preview)
    }
  ]

  /* Всё про шаг 1 — одним сворачиваемым блоком сразу под его кнопкой:
     строка статуса, сводка последнего прогона и очищенный текст источника.
     Свёрнутый блок всё равно показывает выжимку в summary, а при любом
     действии раскрывается сам — иначе клик выглядел бы как «ничего не
     происходит» (ровно эту граблю уже ловили, когда статус жил под спойлером). */
  const headBits = [
    tone === 'error' ? '⚠ ошибка' : busy ? '⏳ ' + busy : report && report.won ? 'готово' : '',
    report && report.won,
    report && report.chars != null ? report.chars + ' симв' : '',
    report && report.lines != null ? report.lines + ' строк' : '',
    report && report.junk_total != null ? 'мусор ' + report.junk_total : ''
  ].filter(Boolean)

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
     тёмной панели даёт сдвиг в 2-3 единицы канала). 12% базы, как было раньше,
     давали блок заметно светлее кнопки — «слишком светлый». */
  const BLOCK_BG = 'color-mix(in oklab, ' + BASE + ' 5%, transparent)'
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
  const CHIP_BOX = 'rounded-md border border-(--ui-border) px-2 py-1 space-y-1'
  const CHIP_BG = 'color-mix(in oklab, ' + BASE + ' 6%, transparent)'
  const CHIP_MUTED = 'text-[10px] leading-snug text-(--ui-text-tertiary, #8a8a8a)'
  const CHIP_CHIEF = 'text-[10px] leading-snug text-(--ui-text-primary)'
  const CHIP_LINK = 'h-6 justify-start px-1 text-[10px] text-(--ui-text-primary)'

  /* Своя категория: её в списке нет по определению — значит её надо завести.
     Каталог создастся при установке, но описание пишем сразу: категория без
     DESCRIPTION.md рождается немой, и агент видит только её имя. */
  const isCustomCat = !!(cats && cats.length) && !cats.includes(cat)
  const catInfo = (catMeta && catMeta[cat]) || null

  /* Кнопки чипсы правят файл описания. Текст сочиняет агент (это LLM-работа),
     панель шлёт интент: kind=desc — написать, kind=desc-fix — обернуть прозу
     в frontmatter, чтобы Hermes её наконец увидел. */
  const sendDesc = (kind) => sendIntent(kind)

  const catChip = !catInfo
    ? null
    : jsxs('div', {
        className: CHIP_BOX,
        style: { backgroundColor: CHIP_BG },
        children: catInfo.desc_state === 'ok'
          ? [
              jsx('div', { className: CHIP_CHIEF, children: catInfo.desc }),
              jsx('div', {
                className: CHIP_MUTED,
                children: 'Это описание Hermes читает в промпте — по нему агент понимает, когда звать категорию.'
              })
            ]
          : catInfo.desc_state === 'no-frontmatter'
            ? [
                jsx('div', {
                  className: CHIP_CHIEF,
                  children: '⚠ Hermes это описание НЕ читает: в файле нет frontmatter — в промпт уйдёт голое имя категории.'
                }),
                jsx('div', {
                  className: CHIP_MUTED,
                  children: 'сейчас в файле: ' + ((catInfo.desc_raw || '').slice(0, 240) || '—')
                }),
                jsx(Button, {
                  variant: 'ghost',
                  disabled: busy === 'desc-fix',
                  onClick: () => sendDesc('desc-fix'),
                  className: CHIP_LINK,
                  children: jsx('span', { children: '🩹 Починить файл — обернуть текст в frontmatter' })
                })
              ]
            : [
                jsx('div', {
                  className: CHIP_CHIEF,
                  children: '⚠ у категории нет DESCRIPTION.md — в промпте агента она идёт без пояснения, просто именем.'
                }),
                jsx(Button, {
                  variant: 'ghost',
                  disabled: busy === 'desc',
                  onClick: () => sendDesc('desc'),
                  className: CHIP_LINK,
                  children: jsx('span', { children: '✍ Дописать описание категории' })
                })
              ]
      })

  const resultBlock = jsxs('details', {
    className: 'rounded border border-(--ui-border) px-2 py-1 text-[0.625rem] leading-snug',
    style: { backgroundColor: BLOCK_BG },
    open: outOpen,
    onToggle: (e) => setOutOpen(!!(e && e.target && e.target.open)),
    children: [
      jsx('summary', {
        className: 'cursor-pointer select-none text-(--ui-text-secondary)',
        children: '📊 Результат разбора' + (headBits.length ? ' · ' + headBits.join(' · ') : ' — пока пусто')
      }),

      /* 1) строка статуса — что именно сделал шаг 1/3 */
      status
        ? jsx('div', {
            className: cn(
              'mt-1 rounded border border-(--ui-border) px-2 py-1',
              tone === 'error' ? 'text-(--ui-text-primary)' : 'text-(--ui-text-secondary)'
            ),
            children: (tone === 'error' ? '⚠ ' : '') + status
          })
        : null,

      /* 2) сводка последнего прогона: стратегия-победитель, объём, путь к файлу */
      report
        ? jsxs('div', {
            className: 'mt-1 flex flex-col gap-0.5 text-(--ui-text-secondary)',
            children: [
              jsx('div', {
                children: 'последний прогон' + (report.at ? ' (' + report.at + ')' : '') +
                  (report.won ? ' · ' + report.won : '')
              }),
              jsx('div', {
                children: [
                  report.chars != null ? report.chars + ' симв' : null,
                  report.words != null ? ' · ' + report.words + ' слов' : null,
                  report.junk_total != null ? ' · мусор ' + report.junk_total : null
                ].filter(Boolean).join('')
              }),
              report.source_file
                ? jsx('div', { className: 'break-all opacity-80', children: report.source_file })
                : null
            ]
          })
        : null,

      /* 3) собственно очищенный текст — то, что уйдёт в пайплайн */
      jsx('div', {
        className: 'mt-2 text-(--ui-text-secondary)',
        children: '🔤 Очищенный текст источника' +
          (textInfo && textInfo.chars != null ? ' · ' + textInfo.chars + ' симв' : '') +
          (textInfo && textInfo.lines != null ? ' · ' + textInfo.lines + ' строк' : '') +
          (textInfo && textInfo.partial ? ' · первые 6 КБ' : '')
      }),
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
            children: 'нажми шаг 1 — вычищенный текст появится здесь'
          }),
      textInfo && textInfo.path
        ? jsx('div', { className: 'break-all pt-1 opacity-70', children: textInfo.path })
        : null,
      textInfo && textInfo.truncated
        /* Фон кнопки — тот же, что у текстового поля (фон панели + data-glass-raised,
           иначе под стеклом он резолвится в transparent и кнопка снова «сливается»).
           Фон задаём обёртке, а не кнопке: у ghost-варианта заливка живёт на hover,
           и inline-стиль кнопки перебил бы его — кнопка стала бы «мёртвой» на наведение. */
        ? jsx('div', {
            'data-glass-raised': '',
            className: 'mt-1 inline-flex rounded',
            style: { backgroundColor: PANEL_BG },
            children: jsx(Button, {
              size: 'sm',
              variant: 'ghost',
              disabled: textBusy,
              onClick: () => loadText(0),
              className: 'h-6 justify-start text-[0.625rem]',
              children: 'показать весь текст'
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

  /* План установки — не украшение: второй клик по шагу 3 ЗАПИСЫВАЕТ в профиль.
     Поэтому сперва видно, что именно изменится: сколько файлов добавится, какие
     перезапишутся, что останется как было, куда лёг бэкап. Раньше панель писала
     в папку молча и, что хуже, «замена» физически накладывала файлы поверх
     старых — теперь режим виден в заголовке и в самом плане. */
  const planRowsList = planRows(preview)
  const planBlock = preview
    ? jsxs('div', {
        className: 'flex flex-col gap-0.5 rounded border px-2 py-1 text-[0.625rem] leading-snug',
        style: {
          backgroundColor: BLOCK_BG,
          borderColor: 'color-mix(in oklab, ' + BASE + ' 22%, transparent)'
        },
        children: [
          jsx('div', {
            className: 'text-(--ui-text-secondary)',
            children: (preview.dry_run === false ? '📦 Установлено · ' : '📦 План установки · ') +
              'режим ' + preview.mode + ' · ' + preview.files + ' файл(ов), ' + preview.kb + ' КБ'
          }),
          jsx('div', { className: 'break-all opacity-80', children: preview.target }),
          preview.dry_run === false
            ? jsx('div', {
                children: '✔ записано ' + ((preview.wrote || []).length) + ' файл(ов)' +
                  (preview.validation && preview.validation.ok === false
                    ? ' · проверка скилла: есть замечания'
                    : ' · проверка скилла: ок')
              })
            : null,
          ...planRowsList.map((line, i) =>
            jsx('div', { className: 'break-all', children: line }, 'plan-' + i)),
          preview.mode === 'replace'
            ? jsx('div', {
                className: 'text-(--ui-text-primary)',
                children: '⚠ ЗАМЕНА: каталог скилла сносится целиком — старых глав не останется.'
              })
            : null,
          preview.warning
            ? jsx('div', { className: 'text-(--ui-text-primary)', children: '⚠ ' + preview.warning })
            : null,
          jsx('div', {
            className: 'opacity-70',
            children: preview.backup
              ? 'бэкап: ' + preview.backup
              : preview.target_exists
                ? 'бэкап снимется перед записью'
                : 'новый скилл — бэкап не нужен'
          })
        ]
      })
    : null

  /* Раскладка по главам — подготовка шага 3: владелец видит, что изменится в
     ТЕКСТЕ скилла, а не только какие файлы лягут. Кнопка отдельная и бесплатная:
     считает Python, в чат ничего не уходит, пока не нажмут «отправить агенту». */
  const chapterRowsList = chapterRows(chapterPlan)
  const chapterBlock = chapterPlan
    ? jsxs('div', {
        className: 'flex flex-col gap-0.5 rounded border px-2 py-1 text-[0.625rem] leading-snug',
        style: {
          backgroundColor: BLOCK_BG,
          borderColor: 'color-mix(in oklab, ' + BASE + ' 22%, transparent)'
        },
        children: [
          jsx('div', {
            className: 'text-(--ui-text-secondary)',
            children: '🧩 План по главам · ' + (chapterPlan.target_exists
              ? 'долив в ' + chapterPlan.category + '/' + chapterPlan.name +
                ' · порог ' + chapterPlan.threshold
              : 'новая папка — сливать не с чем')
          }),
          ...chapterRowsList.map((line, i) =>
            jsx('div', { className: 'break-all', children: line }, 'chap-' + i)),
          jsx('div', {
            className: 'flex flex-wrap gap-1 pt-1',
            children: [
              jsx(Button, {
                size: 'sm',
                variant: 'ghost',
                disabled: chapterBusy,
                onClick: () => sendIntent('plan'),
                className: 'h-6 text-[0.625rem]',
                children: '↗ Отправить агенту: решить, что слить'
              })
            ]
          }),
          jsx('div', {
            className: 'opacity-70',
            children: chapterPlan.target_exists
              ? 'Python дал раскладку и близость — приговор выносит LLM, спорное решает владелец'
              : 'все файлы новые: выбирать не из чего, долив невозможен'
          })
        ]
      })
    : null

  const chapterSlot = jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      jsx('div', {
        className: 'flex rounded',
        style: { backgroundColor: STEP_BG },
        children: jsxs(Button, {
          size: 'sm',
          variant: 'ghost',
          disabled: chapterBusy,
          onClick: runPlan,
          className: 'h-7 justify-start text-xs text-(--ui-text-primary)',
          children: [
            jsx('span', { 'aria-hidden': true, children: '🧩' }),
            jsx('span', { children: '3′. План по главам: что слить, что переписать' })
          ]
        })
      }),
      jsx('span', {
        className: 'pl-1 text-[0.625rem] leading-snug text-(--ui-text-tertiary)',
        children: chapterBusy
          ? 'считаю близость глав…'
          : 'долив: показывает пересечение тем со старыми главами — без записи и без LLM'
      }),
      chapterBlock
    ]
  })

  return jsxs('div', {
    className: cn('flex h-full min-h-0 flex-col gap-3 overflow-y-auto p-3'),
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between gap-2',
        children: [
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx(StatusDot, { tone: dotTone }),
              jsx('span', { className: 'text-xs font-medium text-(--ui-text-primary)', children: 'BOOK → SKILL' })
            ]
          }),
          isWorking
            ? jsx(Badge, { variant: 'warn', children: 'работаю' })
            : core === null
              ? jsx(Badge, { variant: 'muted', children: 'проба ядра…' })
              : core
                ? jsx(Badge, { variant: 'muted', children: 'ядро на связи' })
                : jsx(Badge, { variant: 'warn', children: 'ядро не ответило' })
        ]
      }),
      jsx('div', {
        className: 'text-[0.625rem] leading-snug text-(--ui-text-tertiary)',
        children: 'Шаги 1 и 3 — локальный Python (без LLM, мимо чата). Шаги 2 и разбор — в ЭТОТ чат: там пишет модель.'
      }),

      jsx(Field, {
        label: 'Источник',
        hint: 'URL или путь',
        children: jsx(Input, {
          value: src,
          onChange: (e) => setSrc(e.target.value),
          placeholder: 'https://… или D:/путь/файл.md',
          className: 'h-7 text-xs'
        })
      }),

      jsxs('div', {
        className: 'grid grid-cols-2 gap-2',
        children: [
          jsx(Field, {
            label: 'Категория',
            children: cats && cats.length
              ? jsxs('div', { className: 'space-y-1', children: [
                  jsxs(Select, {
                    value: isCustomCat ? '__custom__' : cat,
                    onValueChange: (v) => setCat(v === '__custom__' ? '' : v),
                    children: [
                      jsx(SelectTrigger, { className: 'h-7 text-xs', children: jsx(SelectValue, {}) }),
                      jsx(SelectContent, {
                        children: [
                          ...cats.map((c) => jsx(SelectItem, { value: c, children: c }, c)),
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
                          jsx('div', { className: CHIP_CHIEF, children: '⚠ категории «' + (cat || '…') + '» в профиле нет — папка создастся при установке.' }),
                          jsx('div', { className: CHIP_MUTED, children: 'Опиши её здесь: без описания Hermes покажет категорию агенту голым именем, и скилл в ней будет труднее найти.' }),
                          jsx(Input, {
                            value: catDesc,
                            onChange: (e) => setCatDesc(e.target.value),
                            placeholder: 'описание категории для Hermes — одной строкой',
                            className: 'h-7 text-xs'
                          })
                        ] })
                      ] })
                    : catChip,
                  catLoose.length
                    ? jsx('div', { className: CHIP_MUTED, children: 'вне категорий (лежат прямо в skills/): ' + catLoose.join(', ') })
                    : null
                ] })
              : jsx(Input, {
                  value: cat,
                  onChange: (e) => setCat(e.target.value),
                  placeholder: cats === null ? 'список грузится…' : 'списка нет — перезапусти dashboard',
                  title: catErr,
                  className: 'h-7 text-xs'
                })
          }),
          jsxs(Field, {
            label: 'Имя скилла',
            hint: existing ? 'занято — долив' : skills ? 'свободно — новый' : '',
            children: [
              jsx(Input, {
                value: name,
                onChange: (e) => setName(e.target.value),
                placeholder: 'python-pathlib',
                list: 'b2s-skill-names',
                title: skillsErr || '',
                className: 'h-7 text-xs'
              }),
              /* Подсказка имён — из профиля, а не выдуманная: на 600 страницах
                 набирать имя руками и угадывать его написание невозможно. */
              jsx('datalist', {
                id: 'b2s-skill-names',
                children: (skills || []).slice(0, 300).map((s) =>
                  jsx('option', { value: s.name, children: s.category || '' }, s.name))
              }),
              jsx('div', {
                className: 'text-[10px] leading-tight text-(--ui-text-tertiary, #8a8a8a)',
                children: existing
                  ? 'уже стоит: ' + (existing.category || 'без категории') + ' · глав ' +
                    existing.chapters + ' · файлов ' + existing.files +
                    ' — новые главы допишутся к нему'
                  : skills === null
                    ? 'список скиллов грузится…'
                    : skillsErr
                      ? 'списка нет — имя соберётся как новый скилл'
                      : 'такого скилла нет — будет новый'
              }),
              existing
                ? jsxs(Select, {
                    value: act,
                    onValueChange: setAct,
                    children: [
                      jsx(SelectTrigger, { className: 'h-6 text-[10px]', children: jsx(SelectValue, {}) }),
                      jsx(SelectContent, {
                        children: ACTS.map((a) => jsx(SelectItem, { value: a.value, children: a.label }, a.value))
                      })
                    ]
                  })
                : null,
              existing && act === 'replace'
                ? jsx('div', {
                    className: 'text-[10px] leading-tight text-(--ui-text-primary)',
                    children: '⚠ каталог скилла будет снесён и залит заново. Перед записью ядро снимет копию в backups/, но подтверждение спрошу ещё раз.'
                  })
                : null
            ]
          })
        ]
      }),

      jsx(Field, {
        label: 'Стратегия загрузки',
        children: jsxs(Select, {
          value: strat,
          onValueChange: setStrat,
          children: [
            jsx(SelectTrigger, { className: 'h-7 text-xs', children: jsx(SelectValue, {}) }),
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
                jsx(SelectTrigger, { className: 'h-7 text-xs', children: jsx(SelectValue, {}) }),
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
                jsx(SelectTrigger, { className: 'h-7 text-xs', children: jsx(SelectValue, {}) }),
                jsx(SelectContent, {
                  children: LANGS.map((l) => jsx(SelectItem, { value: l.value, children: l.label }, l.value))
                })
              ]
            })
          })
        ]
      }),

      jsx('div', { className: 'h-px bg-(--ui-border)' }),

      jsxs('div', {
        className: 'flex flex-col gap-2',
        children: steps.map((s, i) =>
          jsxs(
            'div',
            {
              className: 'flex flex-col gap-1',
              children: [
                /* Три шага — один цвет, слабо-зелёный. Своя константа (не токен темы):
                   тему кнопок владелец будет крутить, а шаги не должны уезжать за ней.
                   Плотность как у прежней кнопки: база 4% + зелёная примесь сверху. */
                jsx('div', {
                  className: 'flex rounded',
                  style: { backgroundColor: STEP_BG },
                  children: jsxs(Button, {
                    size: 'sm',
                    variant: 'ghost',
                    disabled: busy === s.kind,
                    onClick: s.run,
                    className: 'h-7 justify-start text-xs text-(--ui-text-primary)',
                    children: [
                      jsx('span', { 'aria-hidden': true, children: s.icon }),
                      jsx('span', { children: (i + 1) + '. ' + s.title })
                    ]
                  })
                }),
                jsx('span', {
                  className: 'pl-1 text-[0.625rem] leading-snug text-(--ui-text-tertiary)',
                  children: s.note
                }),
                /* всё про шаг 1 (статус + сводка + очищенный текст) — сразу под кнопкой */
                s.kind === 'rerun' ? resultBlock : null,
                /* план записи — под кнопкой шага 3: второй клик пишет в профиль */
                s.kind === 'install' ? planBlock : null,
                /* подготовка долива: раскладка по главам (что слить / переписать) */
                s.kind === 'install' ? chapterSlot : null
              ]
            },
            s.kind
          )
        )
      }),

      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx(Field, {
            label: 'Разобрать черновик',
            hint: 'критика от LLM — идёт в чат',
            children: jsx(Button, {
              size: 'sm',
              variant: 'ghost',
              disabled: busy === 'review',
              onClick: () => sendIntent('review'),
              className: 'h-7 justify-start text-xs',
              style: { backgroundColor: REVIEW_BG },
              children: 'Критика и список правок'
            })
          })
        ]
      }),

      /* Статус, сводка прогона и очищенный текст переехали выше — в один
         сворачиваемый блок сразу под кнопкой шага 1 (resultBlock). Здесь их нет. */

      jsxs('div', {
        className: 'flex flex-col gap-0.5 pt-1 text-[0.625rem] text-(--ui-text-tertiary)',
        children: [
          jsx('span', { className: 'break-all', children: 'REST: /rerun · /install · /plan · /skills · /categories · /text' }),
          jsx('span', { children: 'сессия (для чат-шагов): ' + (focusedId || '—') })
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
