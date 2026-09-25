import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

import {
  ApiError,
  getCrawlProgress,
  getNotifications,
  getRuns,
  type CrawlProgress,
  type Notifications,
} from '../lib/api'
import { onInternalClick } from '../lib/route'
import {
  bellState,
  readSeenAt,
  runHealth,
  statusDetail,
  statusLine,
  writeSeenAt,
  type NotificationKind,
  type RunHealth,
  type RunHistory,
} from '../lib/status'
import { THEMES, type Theme } from '../lib/theme'
import { NotificationsPanel } from '../explore/NotificationsPanel'
import { Icon } from './Icon'
import { Mark, WORDMARK } from './Mark'

/**
 * The top bar (task P6-27) — design-system.md §5, and the bar drawn identically
 * across `ExploreLanding`, `Explore`, `AdminLight` and `Notifications`.
 *
 * 54px. Lockup left, a divider, the nav as tab pills, then — pushed right — the
 * **top-right cluster**, "designed once, used on every screen, in this order":
 * status pill · divider · notifications · settings. No greeting anywhere (§5):
 * one person owns this system, and a welcome line would be addressing them on
 * behalf of nobody.
 *
 * **Translucent over Explore, opaque over Admin** (§5): "translucency means
 * floating above your work; opacity means this is the thing you are reading".
 * On Explore the bar overlays a canvas; on Admin it sits over a document.
 *
 * Pages that need controls *in* the bar — the graph view's search field and
 * view switcher, on the Explore artboard — render them through `<InTopBar>`,
 * which portals into the space between the nav and the cluster. The bar stays
 * one component that every screen gets from `App`, rather than a copy per page.
 */

export const NAV = [
  { path: '/', label: 'Explore', name: 'explore' },
  { path: '/map', label: 'Map', name: 'map' },
  { path: '/gaps', label: 'Gaps', name: 'gaps' },
  { path: '/admin', label: 'Admin', name: 'admin' },
] as const

export type Section = (typeof NAV)[number]['name']

const THEME_LABEL: Record<Theme, string> = { system: 'System', light: 'Light', dark: 'Dark' }

/** How often the cluster re-reads. Slow on purpose: this is a glance, not a monitor. */
export const POLL_MS = 60_000

// --------------------------------------------------------------------------
// The slot pages can render into

const SlotContext = createContext<HTMLElement | null>(null)

export const TopBarSlotProvider = SlotContext.Provider

/** Render children in the top bar, between the nav and the cluster. */
export function InTopBar({ children }: { children: React.ReactNode }) {
  const slot = useContext(SlotContext)
  return slot ? createPortal(children, slot) : null
}

// --------------------------------------------------------------------------
// Data

export interface ClusterData {
  progress: CrawlProgress | null
  runs: RunHistory | null
  notifications: Notifications | null
}

function reasonOf(cause: unknown): string {
  return cause instanceof ApiError ? cause.message : 'the request did not complete.'
}

/**
 * The three reads behind the cluster, each failing on its own.
 *
 * One endpoint down must not blank the others: a closed Admin (503 on
 * `/api/admin/runs`) still leaves queue depth and notifications worth showing.
 */
export function useClusterData(pollMs: number = POLL_MS): ClusterData {
  const [data, setData] = useState<ClusterData>({ progress: null, runs: null, notifications: null })

  useEffect(() => {
    let controller = new AbortController()

    function load() {
      controller.abort()
      controller = new AbortController()
      const { signal } = controller
      getCrawlProgress({ signal })
        .then((progress) => setData((d) => ({ ...d, progress })))
        .catch(() => {
          if (!signal.aborted) setData((d) => ({ ...d, progress: null }))
        })
      getRuns({ limit: 10 }, { signal })
        .then((body) => setData((d) => ({ ...d, runs: { kind: 'read', rows: body.rows } })))
        .catch((cause: unknown) => {
          if (!signal.aborted) setData((d) => ({ ...d, runs: { kind: 'unreadable', reason: reasonOf(cause) } }))
        })
      getNotifications({ limit: 50 }, { signal })
        .then((notifications) => setData((d) => ({ ...d, notifications })))
        .catch(() => {
          if (!signal.aborted) setData((d) => ({ ...d, notifications: null }))
        })
    }

    load()
    const timer = window.setInterval(load, pollMs)
    return () => {
      window.clearInterval(timer)
      controller.abort()
    }
  }, [pollMs])

  return data
}

// --------------------------------------------------------------------------
// The bar

export interface TopBarProps {
  section: Section | null
  /** §5: translucent over Explore surfaces, opaque over Admin. */
  translucent: boolean
  theme: Theme
  onTheme: (theme: Theme) => void
  /** Where `<InTopBar>` content lands. */
  slotRef?: (element: HTMLElement | null) => void
  /** Injected in tests; `useClusterData` otherwise. */
  data?: ClusterData
}

export function TopBar(props: TopBarProps) {
  // Hooks cannot be conditional, so the live reads always run in the app and a
  // test passes `data` to a bar that ignores them.
  return props.data ? <Bar {...props} data={props.data} /> : <LiveBar {...props} />
}

function LiveBar(props: TopBarProps) {
  const data = useClusterData()
  return <Bar {...props} data={data} />
}

function Bar({ section, translucent, theme, onTheme, slotRef, data }: TopBarProps & { data: ClusterData }) {
  return (
    <header
      data-surface={translucent ? 'translucent' : 'opaque'}
      className={`sticky top-0 z-40 flex h-[54px] shrink-0 items-center gap-3 border-b border-line px-3 sm:gap-5 sm:px-[18px] ${
        translucent ? 'bg-surface/90 backdrop-blur-[10px]' : 'bg-surface'
      }`}
    >
      <a
        href="/"
        onClick={onInternalClick('/')}
        className="flex shrink-0 items-center gap-[9px] text-text"
        aria-label={`${WORDMARK} — Explore`}
      >
        {/* The artboards set the bar's lockup without §1's hairline: at 22px
            the compact mark and a 15px wordmark read as one signature, and a
            rule between them would sit beside the divider that follows. */}
        <Mark size={22} />
        <span className="hidden font-sans text-[15px] font-medium sm:inline leading-none tracking-[-0.004em]">{WORDMARK}</span>
      </a>

      <span aria-hidden="true" className="h-[22px] w-px shrink-0 bg-line" />

      <nav className="flex shrink-0 gap-1" aria-label="Sections">
        {NAV.map(({ path, label, name }) => (
          <a
            key={path}
            href={path}
            onClick={onInternalClick(path)}
            aria-current={section === name ? 'page' : undefined}
            className={`px-2 py-1.5 sm:px-3 font-sans text-[12.5px] leading-[1.35] ${
              section === name ? 'bg-surface-raised text-text' : 'text-text-faint hover:text-text-muted'
            }`}
          >
            {label}
          </a>
        ))}
      </nav>

      <div ref={slotRef} className="flex min-w-0 flex-1 items-center gap-3" data-role="topbar-slot" />

      <div className="flex shrink-0 items-center gap-2.5">
        <StatusPill progress={data.progress} runs={data.runs} />
        <span aria-hidden="true" className="h-5 w-px bg-line" />
        <Bell notifications={data.notifications} />
        <Settings theme={theme} onTheme={onTheme} />
      </div>
    </header>
  )
}

// --------------------------------------------------------------------------
// Status pill

const DOT: Record<RunHealth, string> = {
  ok: 'bg-accent-graph',
  failed: 'bg-accent-attention',
  // Hollow, not cyan: a health check that could not run is not a pass.
  unknown: 'border border-text-faint',
}

export function StatusPill({ progress, runs }: { progress: CrawlProgress | null; runs: RunHistory | null }) {
  const health = runHealth(runs)
  const detail = statusDetail(progress, health, runs)

  return (
    <a
      href="/admin"
      onClick={onInternalClick('/admin')}
      title={detail}
      data-health={health}
      className="hidden items-center gap-[7px] border border-line px-2.5 py-[5px] font-mono text-[10.5px] leading-none whitespace-nowrap text-text-muted hover:border-line-strong md:flex"
    >
      <span aria-hidden="true" className={`block h-1.5 w-1.5 shrink-0 rounded-full ${DOT[health]}`} />
      <span>{progress ? statusLine(progress) : 'status unavailable'}</span>
      {/* §5: state in form, not only colour. The brass dot alone would be
          invisible to a reader who cannot see brass. */}
      {health === 'failed' ? <span className="text-accent-attention">· run failed</span> : null}
      <span className="sr-only">. {detail}</span>
    </a>
  )
}

// --------------------------------------------------------------------------
// Popovers

/** Close on Escape and on a click outside. Returns the ref for the wrapper. */
function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    if (!open) return
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') close()
    }
    function onPointer(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) close()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onPointer)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onPointer)
    }
  }, [open, close])
  return ref
}

const POPOVER =
  'absolute right-0 top-[calc(100%+20px)] z-50 border border-line-strong bg-surface ' +
  'shadow-[0_20px_46px_rgba(4,8,18,0.4)]'

// --------------------------------------------------------------------------
// Notifications

export function Bell({ notifications }: { notifications: Notifications | null }) {
  const [open, setOpen] = useState(false)
  const [seenAt, setSeenAt] = useState<string | null>(readSeenAt)
  const [kind, setKind] = useState<NotificationKind | null>(null)
  const close = useCallback(() => setOpen(false), [])
  const ref = useDismiss(open, close)

  const { count, tone } = bellState(notifications?.notifications ?? [], seenAt)
  const alert = tone === 'attention'

  function toggle() {
    if (!open) {
      // Opening is the acknowledgement. The panel is still filtered by type,
      // not by read state — this only moves the badge.
      const now = new Date()
      writeSeenAt(now)
      setSeenAt(now.toISOString())
    }
    setOpen(!open)
  }

  const label =
    count === 0
      ? 'Notifications, nothing new'
      : `Notifications, ${count} new${alert ? ', including an alert' : ''}`

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={label}
        className={`relative flex p-1 text-text/80 hover:text-text ${open ? 'bg-surface-raised' : ''}`}
      >
        <Icon name="notifications" size={18} />
        {count > 0 ? (
          <span
            data-tone={tone}
            aria-hidden="true"
            className={`absolute -right-1 -top-0.5 flex h-[15px] min-w-[15px] items-center justify-center px-[3px] font-mono text-[9px] font-semibold leading-none text-surface ${
              alert ? 'bg-accent-attention' : 'bg-accent-graph'
            }`}
          >
            {count > 99 ? '99+' : count}
          </span>
        ) : null}
      </button>

      {open ? (
        <div role="dialog" aria-label="Notifications" className={`${POPOVER} w-[440px] max-w-[calc(100vw-32px)]`}>
          {notifications ? (
            <NotificationsPanel
              notifications={notifications.notifications}
              countsByType={notifications.counts_by_type}
              active={kind}
              onFilter={setKind}
            />
          ) : (
            <p className="px-[18px] py-5 text-[length:var(--text-small)] text-text-muted">
              Notifications could not be read from the API. The daily digest is unaffected.
            </p>
          )}
        </div>
      ) : null}
    </div>
  )
}

// --------------------------------------------------------------------------
// Settings

/**
 * Settings — for now, the theme. §5: "Settings is where an account menu goes
 * once auth stops being Cloudflare Access", so this is a menu rather than a
 * single toggle even while it holds one choice.
 */
export function Settings({ theme, onTheme }: { theme: Theme; onTheme: (theme: Theme) => void }) {
  const [open, setOpen] = useState(false)
  const close = useCallback(() => setOpen(false), [])
  const ref = useDismiss(open, close)

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="Settings"
        className={`flex p-1 text-text/80 hover:text-text ${open ? 'bg-surface-raised' : ''}`}
      >
        <Icon name="steering" size={18} />
      </button>

      {open ? (
        <div role="dialog" aria-label="Settings" className={`${POPOVER} w-[260px] p-4`}>
          <p
            id="theme-label"
            className="font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint"
          >
            Theme
          </p>
          <div role="radiogroup" aria-labelledby="theme-label" className="mt-2.5 flex border border-line">
            {THEMES.map((option) => (
              <button
                key={option}
                type="button"
                role="radio"
                aria-checked={theme === option}
                onClick={() => onTheme(option)}
                className={`flex-1 border-r border-line py-1.5 font-sans text-[12px] last:border-r-0 ${
                  theme === option ? 'bg-surface-raised text-text' : 'text-text-faint hover:text-text-muted'
                }`}
              >
                {THEME_LABEL[option]}
              </button>
            ))}
          </div>
          <p className="mt-2.5 font-mono text-[10px] leading-[1.5] text-text-faint">
            {theme === 'system'
              ? 'Follows this machine. Dark is the flagship.'
              : `${THEME_LABEL[theme]}, whatever this machine is set to.`}
          </p>
          {/* About is not a section, so it is not a tab: the nav is where the
              work is, and this is where the system describes itself. */}
          <div className="mt-4 border-t border-line pt-3">
            <a
              href="/about"
              onClick={(event) => {
                onInternalClick('/about')(event)
                close()
              }}
              className="font-sans text-[12.5px] text-text-muted hover:text-text"
            >
              About {WORDMARK}
            </a>
          </div>
        </div>
      ) : null}
    </div>
  )
}
