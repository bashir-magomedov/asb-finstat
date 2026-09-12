import { useEffect, useRef, useState } from 'react'
import { COUNTRIES, flagUrl, type Country } from './countries'

type Company = { name: string; description?: string }
type StepUpdate = {
  type: 'step_update'
  step: string
  status: 'running' | 'done' | 'error' | 'skipped'
  message: string
  data?: unknown
}
type CompaniesMsg = { type: 'companies'; requestId: number; companies: Company[]; error?: string }
type ServerMessage = StepUpdate | CompaniesMsg

const STEP_LABELS: Record<string, string> = {
  pipeline: 'Pipeline',
  find_statements: '#2 Find statements',
  language_check: '#3 Language check',
  translate: '#4 Translate',
  extract: '#5 Extract',
}

const STATUS_ICONS: Record<StepUpdate['status'], string> = {
  running: '●',
  done: '✓',
  error: '✕',
  skipped: '○',
}

type Mood = 'happy' | 'sad' | 'busy' | 'curious'

const MOOD_LABELS: Record<Mood, string> = {
  happy: 'connected',
  sad: 'disconnected',
  busy: 'working…',
  curious: 'searching…',
}

function Robot({ mood }: { mood: Mood }) {
  return (
    <svg className={`robot ${mood}`} viewBox="0 0 72 84" width="200" height="233" aria-hidden="true">
      <defs>
        <clipPath id="visor">
          <rect x="21" y="13" width="30" height="16" rx="5" />
        </clipPath>
      </defs>
      <g className="head">
        <line x1="36" y1="4" x2="36" y2="9" stroke="#dde3f0" strokeWidth="2" />
        <circle className="antenna-tip" cx="36" cy="3" r="2.4" />
        <rect x="12.5" y="16" width="4" height="12" rx="2" fill="#dde3f0" />
        <rect x="55.5" y="16" width="4" height="12" rx="2" fill="#dde3f0" />
        <rect x="16" y="8" width="40" height="26" rx="8" fill="#eef1f8" />
        <rect x="21" y="13" width="30" height="16" rx="5" fill="#0c0f16" />
        <g className="eyes" clipPath="url(#visor)">
          <g className="eye-group left">
            <rect className="eye" x="26.5" y="16" width="7" height="10" rx="2.5" />
            <path className="smile" d="M26 24.5 Q30 18 34 24.5" />
          </g>
          <g className="eye-group right">
            <rect className="eye" x="38.5" y="16" width="7" height="10" rx="2.5" />
            <path className="smile" d="M38 24.5 Q42 18 46 24.5" />
          </g>
        </g>
      </g>
      <g className="body">
        <rect className="arm left" x="13" y="46" width="5" height="16" rx="2.5" fill="#dde3f0" />
        <rect className="arm right" x="54" y="46" width="5" height="16" rx="2.5" fill="#dde3f0" />
        <path
          d="M26 40 L46 40 L52 48 L52 66 Q52 70 48 70 L24 70 Q20 70 20 66 L20 48 Z"
          fill="#eef1f8"
        />
        <rect className="chest" x="30" y="47" width="12" height="6" rx="2" />
        <line x1="22" y1="60" x2="50" y2="60" stroke="#d3dae8" strokeWidth="1.5" />
      </g>
    </svg>
  )
}

function Flag({ code, className }: { code: string; className?: string }) {
  return (
    <img
      className={className ?? 'flag'}
      src={flagUrl(code)}
      alt=""
      onError={(e) => (e.currentTarget.style.visibility = 'hidden')}
    />
  )
}

function CountrySelect({ value, onChange }: { value: Country | null; onChange: (c: Country) => void }) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onClick)
    return () => document.removeEventListener('mousedown', onClick)
  }, [])

  const list = COUNTRIES.filter((c) => c.name.toLowerCase().includes(filter.trim().toLowerCase()))

  return (
    <div className="country-select" ref={ref}>
      <button
        type="button"
        className={`field ${open ? 'open' : ''}`}
        onClick={() => {
          setOpen(!open)
          setFilter('')
        }}
      >
        {value ? (
          <span className="value">
            <Flag code={value.code} />
            {value.name}
          </span>
        ) : (
          <span className="placeholder">Select a country…</span>
        )}
        <span className="chevron">▾</span>
      </button>
      {open && (
        <div className="dropdown">
          <input
            autoFocus
            placeholder="Filter countries…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <ul>
            {list.map((c) => (
              <li
                key={c.code}
                className={value?.code === c.code ? 'active' : ''}
                onClick={() => {
                  onChange(c)
                  setOpen(false)
                }}
              >
                <Flag code={c.code} />
                {c.name}
              </li>
            ))}
            {list.length === 0 && <li className="empty">No matches</li>}
          </ul>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [connected, setConnected] = useState(false)
  const [country, setCountry] = useState<Country | null>(null)
  const [query, setQuery] = useState('')
  const [companies, setCompanies] = useState<Company[]>([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState('')
  const [selected, setSelected] = useState<Company | null>(null)
  const [events, setEvents] = useState<StepUpdate[]>([])
  const wsRef = useRef<WebSocket | null>(null)
  const requestId = useRef(0)

  useEffect(() => {
    let ws: WebSocket
    let closed = false
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws`)
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        if (!closed) setTimeout(connect, 1000)
      }
      ws.onmessage = (e) => {
        const msg: ServerMessage = JSON.parse(e.data)
        if (msg.type === 'companies') {
          if (msg.requestId === requestId.current) {
            setCompanies(msg.companies)
            setSearchError(msg.error ?? '')
            setSearching(false)
          }
        } else if (msg.type === 'step_update') {
          setEvents((prev) => [...prev, msg])
        }
      }
    }
    connect()
    return () => {
      closed = true
      ws.close()
    }
  }, [])

  // AI call #1: debounced typeahead once 2+ characters are typed
  useEffect(() => {
    if (!country || query.trim().length < 2 || selected) {
      setCompanies([])
      setSearching(false)
      return
    }
    const t = setTimeout(() => {
      const id = ++requestId.current
      setSearching(true)
      setSearchError('')
      wsRef.current?.send(
        JSON.stringify({ type: 'search_companies', country: country.name, query: query.trim(), requestId: id }),
      )
    }, 300)
    return () => clearTimeout(t)
  }, [country, query, selected])

  useEffect(() => {
    if (events.length === 0) return
    window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'smooth' })
  }, [events])

  const selectCompany = (c: Company) => {
    if (!country) return
    setSelected(c)
    setCompanies([])
    setQuery(c.name)
    setEvents([])
    wsRef.current?.send(JSON.stringify({ type: 'run_pipeline', country: country.name, company: c }))
  }

  const pipelineActive =
    events.length > 0 &&
    !events.some((e) => e.step === 'pipeline' && (e.status === 'done' || e.status === 'error'))
  const mood: Mood = !connected ? 'sad' : searching ? 'curious' : pipelineActive ? 'busy' : 'happy'

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">ASB</span>
          <div>
            <h1>Finstat</h1>
            <span className="team">team Friendly Strangers</span>
          </div>
        </div>
      </header>

      <div className="robot-dock">
        <Robot mood={mood} />
        <span className="status-label">{MOOD_LABELS[mood]}</span>
      </div>

      <section className="card controls">
        <div className="fieldgroup">
          <span className="caption">Country</span>
          <CountrySelect
            value={country}
            onChange={(c) => {
              setCountry(c)
              setSelected(null)
              setQuery('')
              setCompanies([])
              setEvents([])
            }}
          />
        </div>

        <div className="fieldgroup">
          <span className="caption">Company</span>
          <div className="typeahead">
            <input
              value={query}
              disabled={!country}
              placeholder={country ? 'Start typing a company name (2+ chars)…' : 'Pick a country first'}
              onChange={(e) => {
                setQuery(e.target.value)
                setSelected(null)
              }}
            />
            {searching && <span className="spinner" />}
            {searchError && <div className="hint error">{searchError}</div>}
            {companies.length > 0 && (
              <ul className="suggestions">
                {companies.map((c) => (
                  <li key={c.name} onClick={() => selectCompany(c)}>
                    <span className="avatar">{c.name[0]}</span>
                    <span className="text">
                      <strong>{c.name}</strong>
                      {c.description && <span>{c.description}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      {selected && country && (
        <section className="card activity">
          <h2>
            <Flag code={country.code} />
            {selected.name}
            <span className="sub">agent activity</span>
          </h2>
          <ol>
            {events.map((e, i) => (
              <li key={i} className={`event ${e.status}`}>
                <span className="icon">{STATUS_ICONS[e.status]}</span>
                <span className="step">{STEP_LABELS[e.step] ?? e.step}</span>
                <span className="msg">{e.message}</span>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  )
}
