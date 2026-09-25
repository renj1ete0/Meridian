import { useEffect, useState } from 'react'

import packageJson from '../../package.json'
import { ApiError, corpusStats, type CorpusStats } from '../lib/api'
import { Lockup } from '../ui/Mark'

/**
 * The About screen (task P6-40; design `About`).
 *
 * design-system.md §1: the tagline "appears on the About screen and nowhere
 * else" — repeated, it stops being a statement and becomes decoration. So this
 * is the one component that holds it, and `tests/about.test.tsx` checks no
 * other source file does.
 *
 * Everything on it that could go stale is read, not written: the build is the
 * version the web bundle was built at (`package.json`, which mirrors the root
 * `VERSION`), and the topics and counts come from `/api/explore/stats`, the
 * read-only route the landing already uses. The two statements that are not
 * read — public sources only, self-hosted — are properties of the design, not
 * of a deployment.
 */

export const TAGLINE = 'A line to measure everything else against.'

/** What the web bundle was built at. */
export const BUILD_VERSION: string = packageJson.version

type StatsState =
  | { kind: 'loading' }
  | { kind: 'read'; stats: CorpusStats }
  | { kind: 'unreadable'; reason: string }

/** `walkability`, `on-demand-bus` → "walkability and on demand bus", as the rail labels them. */
export function topicsProse(topics: readonly string[]): string | null {
  const words = topics.map((t) => t.replaceAll('-', ' ').replaceAll('_', ' '))
  if (words.length === 0) return null
  if (words.length === 1) return words[0]!
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
}

/** The corpus row: what is held, in the words the landing uses. */
export function corpusLine(stats: CorpusStats): string {
  const n = (value: number) => value.toLocaleString('en')
  return [
    `${n(stats.sources)} sources`,
    `${n(stats.searchable_chunks)} searchable passages`,
    `${n(stats.entities)} nodes`,
    `${n(stats.edges)} edges`,
  ].join(' · ')
}

export function AboutPage() {
  const [state, setState] = useState<StatsState>({ kind: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    corpusStats({}, { signal: controller.signal })
      .then((stats) => setState({ kind: 'read', stats }))
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setState({
          kind: 'unreadable',
          reason: cause instanceof ApiError ? cause.message : 'The API did not answer.',
        })
      })
    return () => controller.abort()
  }, [])

  const topics = state.kind === 'read' ? state.stats.topics : []
  const prose = topicsProse(topics)

  const unread = (
    <span className="text-text-faint">{state.kind === 'loading' ? 'reading…' : 'unavailable'}</span>
  )

  return (
    <div
      className="relative min-h-[calc(100vh-54px)] overflow-hidden bg-ground"
      style={{
        // The artboard's graticule: the canvas grid, at the strength of a
        // printed rule, so the page reads as the ground the mark stands on.
        backgroundImage:
          'repeating-linear-gradient(90deg, color-mix(in srgb, var(--line) 26%, transparent) 0 1px, transparent 1px 88px),' +
          'repeating-linear-gradient(0deg, color-mix(in srgb, var(--line) 20%, transparent) 0 1px, transparent 1px 88px)',
      }}
    >
      <div className="relative mx-auto min-h-[calc(100vh-54px)] max-w-[1080px] px-4 pb-24 pt-16 sm:px-[68px] sm:pt-[78px]">
        {/* The meridian: a line through the mark's axis, top to bottom. */}
        <span
          aria-hidden="true"
          data-role="meridian"
          className="absolute inset-y-0 left-[54px] w-px bg-accent-graph/20 sm:left-[106px]"
        />

        <article className="relative flex max-w-[620px] flex-col gap-[30px]">
          <header className="flex flex-col gap-5">
            <h1 className="m-0">
              <Lockup size={76} wordmarkSize={49} gap={24} />
            </h1>
            <p className="text-[21px] font-light leading-[1.35] text-text-muted [text-wrap:pretty] sm:pl-[125px]">
              {TAGLINE}
            </p>
          </header>

          <div aria-hidden="true" className="h-px bg-line" />

          <p className="max-w-[60ch] text-[length:var(--text-body)] leading-[1.66] text-text/85 [text-wrap:pretty]">
            Meridian crawls public sources{prose ? ` on ${prose}` : ''}, and relates what it finds into one graph.
            Every claim keeps the chunk it came from. Where two sources disagree, both are kept and the pair is
            marked — the system does not resolve the conflict on your behalf.
          </p>

          <dl className="m-0 flex flex-col gap-[9px]">
            <Row label="Build">{BUILD_VERSION}</Row>
            <Row label="Topics">
              {state.kind === 'read' ? (topics.length ? topics.join(' · ') : 'none configured') : unread}
            </Row>
            <Row label="Corpus">{state.kind === 'read' ? corpusLine(state.stats) : unread}</Row>
            <Row label="Sources">public only</Row>
            <Row label="Host">self-hosted, unattended</Row>
          </dl>

          {state.kind === 'unreadable' ? (
            <p className="text-[length:var(--text-small)] text-accent-attention">
              Topics and counts could not be read. {state.reason}
            </p>
          ) : null}
        </article>
      </div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4">
      <dt className="w-24 shrink-0 pt-[2px] font-mono text-[9px] uppercase tracking-[0.15em] text-text-faint">
        {label}
      </dt>
      <dd className="m-0 min-w-0 font-mono text-[11.5px] text-text/85">{children}</dd>
    </div>
  )
}
