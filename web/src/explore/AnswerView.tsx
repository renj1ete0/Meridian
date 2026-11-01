import { useEffect, useState } from 'react'

import { BUTTON_SECONDARY, FIELD } from '../admin/ui'
import { ApiError } from '../lib/api'
import {
  coverageWord,
  findMoreQuery,
  getAnswer,
  queueFindMore,
  type Answer,
  type AnswerGroup,
  type AnswerItem,
  type AnswerParams,
} from '../lib/answer'
import { answerBrief } from '../lib/brief'
import { searchFilters, type FindFilters } from '../lib/find'
import { readable } from '../lib/readable'
import { dayOf } from '../lib/time'
import { hrefForSource, onInternalClick } from '../lib/route'
import { TierChip, type SourceTier } from '../ui/Tier'

/**
 * A question answered as evidence: each source's own best passage, grouped by country when
 * the question is about places and one list when it is not (`B-241`), each heading a count.
 * Nothing is written by a model. See docs/features/web-app.md#the-answer-view.
 */

/** Plain words for tiers, in running text. */
const TIER_WORDS: Record<SourceTier, string> = {
  government: 'government',
  peer_reviewed: 'peer-reviewed',
  preprint: 'preprint',
  institutional: 'institutional',
  press: 'press',
  informal: 'informal',
}

/** Order tiers are listed in a mix line: primary material first. */
const TIER_ORDER: readonly SourceTier[] = [
  'government',
  'peer_reviewed',
  'preprint',
  'institutional',
  'press',
  'informal',
]

function plural(n: number, word: string, many = `${word}s`): string {
  return `${n.toLocaleString('en')} ${n === 1 ? word : many}`
}

/** `2 government · 1 press`, primary tiers first. */
export function mixLine(mix: Readonly<Record<string, number>>): string {
  const known = TIER_ORDER.filter((tier) => (mix[tier] ?? 0) > 0).map((tier) => `${mix[tier]} ${TIER_WORDS[tier]}`)
  const other = Object.entries(mix)
    .filter(([tier, n]) => !(TIER_ORDER as readonly string[]).includes(tier) && n > 0)
    .map(([tier, n]) => `${n} ${tier}`)
  return [...known, ...other].join(' · ')
}

/** The heading's count line: sources, publishers, mix, newest. */
export function countLine(group: AnswerGroup): string {
  const parts = [plural(group.sources, 'source')]
  if (group.publishers !== group.sources) parts.push(`${plural(group.publishers, 'publisher')}`)
  parts.push(mixLine(group.tier_mix))
  parts.push(group.newest ? `newest ${group.newest}` : 'no dates')
  return parts.join(' · ')
}

/** Why a group is not well-sourced, naming each part of the rule it misses. */
export function thinReason(
  group: AnswerGroup,
  answer: Pick<Answer, 'strong_min_publishers' | 'strong_needs_tiers'>,
): string {
  const missing: string[] = []
  if (group.publishers < answer.strong_min_publishers) {
    missing.push(
      `${group.publishers === 1 ? 'one publisher' : `${group.publishers} publishers`}, fewer than ${answer.strong_min_publishers}`,
    )
  }
  const primary = answer.strong_needs_tiers.some((tier) => (group.tier_mix[tier] ?? 0) > 0)
  if (!primary) {
    missing.push(`no ${answer.strong_needs_tiers.map((t) => TIER_WORDS[t] ?? t).join(' or ')} source`)
  }
  return `Not yet well-sourced: ${missing.join('; ')}.`
}

function anchor(group: AnswerGroup): string {
  return `answer-${group.code ?? 'unplaced'}`
}

// --------------------------------------------------------------------------
// The fetch
// --------------------------------------------------------------------------

export interface AnswerViewProps {
  question: string
  /** Everything Find is narrowed by; the answer runs the same search. */
  filters: FindFilters
  /** Read one country's sources as passages (`B-175`): the same search, narrowed to it. */
  onReadPlace?: (code: string) => void
  /** Read the whole one-list answer as passages: the same search, not narrowed. */
  onReadAll?: () => void
  /** Told whether each answer matched words only, so the search field can say so. */
  onWordsOnly?: (wordsOnly: boolean) => void
}

type State = { phase: 'loading' } | { phase: 'done'; answer: Answer } | { phase: 'failed'; message: string }

export function AnswerView({ question, filters, onReadPlace, onReadAll, onWordsOnly }: AnswerViewProps) {
  const [state, setState] = useState<State>({ phase: 'loading' })
  // Serialised, so a new object with the same filters does not refetch.
  const wireKey = JSON.stringify(searchFilters(filters))

  useEffect(() => {
    const controller = new AbortController()
    setState({ phase: 'loading' })
    getAnswer({ q: question, ...(JSON.parse(wireKey) as Omit<AnswerParams, 'q'>) }, { signal: controller.signal })
      .then((answer) => setState({ phase: 'done', answer }))
      .catch((cause: unknown) => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setState({
          phase: 'failed',
          message: cause instanceof ApiError ? cause.message : 'The answer could not be assembled.',
        })
      })
    return () => controller.abort()
  }, [question, wireKey])

  useEffect(() => {
    if (state.phase === 'done') onWordsOnly?.(state.answer.degraded)
  }, [state, onWordsOnly])

  if (state.phase === 'loading') {
    return <p className="font-mono text-[10.5px] text-text-faint">Gathering the evidence.</p>
  }
  if (state.phase === 'failed') {
    return <p className="border border-line-strong bg-surface p-4 text-[13px] text-text">{state.message}</p>
  }
  return (
    <AnswerBody
      answer={state.answer}
      question={question}
      topic={filters.topics[0] ?? state.answer.topics[0] ?? null}
      onReadPlace={onReadPlace}
      onReadAll={onReadAll}
    />
  )
}

// --------------------------------------------------------------------------
// The page
// --------------------------------------------------------------------------

export function AnswerBody({
  answer,
  question,
  topic,
  onReadPlace,
  onReadAll,
}: {
  answer: Answer
  question: string
  /** What a find-more search is filed under. */
  topic: string | null
  onReadPlace?: (code: string) => void
  onReadAll?: () => void
}) {
  const nothing = answer.groups.length === 0 && answer.unplaced === null && answer.ungrouped === null
  const listed = answer.grouped_by === 'none' ? answer.ungrouped : null

  return (
    <div className="flex flex-col gap-6" data-view="answer">
      {answer.degraded && answer.degraded_reason ? (
        <p className="font-mono text-[10.5px] leading-[1.5] text-text-faint">{answer.degraded_reason}</p>
      ) : null}

      {nothing ? (
        <p className="text-[13.5px] text-text-muted">No source matched {`"${question}"`}.</p>
      ) : (
        <>
          {listed ? (
            <EvidenceStrip answer={answer} listed={listed} onReadPlace={onReadPlace} />
          ) : (
            <CoverageStrip answer={answer} />
          )}
          <BriefLink answer={answer} question={question} />
        </>
      )}

      {listed ? (
        <GroupSection group={listed} answer={answer} question={question} topic={topic} rated onReadAll={onReadAll} />
      ) : null}

      {listed
        ? null
        : answer.groups.map((group) => (
            <GroupSection
              key={group.code}
              group={group}
              answer={answer}
              question={question}
              topic={topic}
              onReadPlace={onReadPlace}
            />
          ))}

      {answer.unplaced && !listed ? (
        <GroupSection group={answer.unplaced} answer={answer} question={question} topic={topic} />
      ) : null}

      <FindMoreAnywhere question={question} topic={topic} />
    </div>
  )
}

const LABEL = 'font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint'

/**
 * The answer as a Markdown brief to take away (`B-206`): one link, under the coverage, so it
 * is there when wanted and costs one line when not.
 */
export function BriefLink({ answer, question }: { answer: Answer; question: string }) {
  const [href, setHref] = useState<string | null>(null)
  useEffect(() => {
    const text = answerBrief(answer, question, window.location.origin, dayOf(new Date().toISOString()))
    const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown' }))
    setHref(url)
    return () => URL.revokeObjectURL(url)
  }, [answer, question])
  if (!href) return null
  return (
    <p className="-mt-3 font-mono text-[10.5px] text-text-faint">
      <a href={href} download={`${briefName(question)}.md`} className="text-accent-graph hover:underline">
        download as a brief (.md)
      </a>{' '}
      · every source quoted and cited, nothing generated
    </p>
  )
}

/** A file name from the question: its words, lower case, hyphenated, cut short. */
export function briefName(question: string): string {
  const slug = question
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60)
    .replace(/-+$/, '')
  return slug ? `meridian-${slug}` : 'meridian-brief'
}

/** Countries the question names that no source here is about, by name. */
export function placesMissing(answer: Pick<Answer, 'places_named' | 'groups'>): string[] {
  const present = new Set(answer.groups.map((g) => g.code))
  return answer.places_named.filter((place) => !present.has(place.code)).map((place) => place.name)
}

function namesLine(names: readonly string[]): string {
  return names.length < 2 ? (names[0] ?? '') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`
}

export function CoverageStrip({ answer }: { answer: Answer }) {
  const strong = answer.groups.filter((g) => g.coverage === 'strong').length
  const missing = placesMissing(answer)
  return (
    <section aria-label="Coverage by country" className="flex flex-col gap-2.5 border border-line bg-surface px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className={LABEL}>Where the evidence is</p>
        <p className="font-mono text-[10.5px] text-text-faint">
          {plural(answer.groups.length, 'country', 'countries')} · {strong} well-sourced · from{' '}
          {plural(answer.sources_considered, 'source')}
        </p>
      </div>
      <p className="text-[12.5px] text-text-muted">{answer.grouping_reason}</p>
      {missing.length > 0 ? (
        <p className="text-[12.5px] text-text">
          No source here is about {namesLine(missing)}, which the question names.
        </p>
      ) : null}
      <ul className="flex flex-wrap gap-1.5">
        {answer.groups.map((group) => (
          <li key={group.code}>
            <CoverageChip group={group} answer={answer} />
          </li>
        ))}
        {answer.unplaced ? (
          <li>
            <a
              href={`#${anchor(answer.unplaced)}`}
              data-coverage="unplaced"
              className="inline-flex items-center gap-1.5 border border-line px-2 py-[3px] font-mono text-[10.5px] leading-[1.4] text-text-faint hover:border-line-strong"
            >
              <span className="tabular-nums">{answer.unplaced.sources}</span> with no place
            </a>
          </li>
        ) : null}
      </ul>
      <p className="max-w-[80ch] text-[12px] leading-[1.5] text-text-muted">{answer.coverage_rule}</p>
    </section>
  )
}

/**
 * The answer as one list (`B-241`): its counts, why it is not grouped, and where its sources
 * are about as a plain tally, each country a way into its passages rather than a verdict.
 */
export function EvidenceStrip({
  answer,
  listed,
  onReadPlace,
}: {
  answer: Answer
  listed: AnswerGroup
  onReadPlace?: (code: string) => void
}) {
  return (
    <section aria-label="The evidence" className="flex flex-col gap-2.5 border border-line bg-surface px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className={LABEL}>The evidence</p>
        <p className="font-mono text-[10.5px] text-text-faint">
          {plural(listed.sources, 'source')} · {plural(listed.publishers, 'publisher')} · {coverageWord(listed, answer)}
        </p>
      </div>
      <p className="text-[12.5px] text-text-muted">{answer.grouping_reason}</p>
      {answer.groups.length > 0 ? (
        <p className="font-mono text-[10.5px] leading-[1.6] text-text-faint" data-tally="places">
          About:{' '}
          {answer.groups.map((group, index) => (
            <span key={group.code}>
              {index > 0 ? ' · ' : null}
              {onReadPlace && group.code ? (
                <button
                  type="button"
                  onClick={() => onReadPlace(group.code!)}
                  title={`Read the ${group.name} sources as passages`}
                  className="hover:text-accent-graph hover:underline"
                >
                  {group.name} {group.sources}
                </button>
              ) : (
                `${group.name} ${group.sources}`
              )}
            </span>
          ))}
          {answer.unplaced ? ` · ${answer.unplaced.sources} with no place` : null}
        </p>
      ) : null}
      <p className="max-w-[80ch] text-[12px] leading-[1.5] text-text-muted">{answer.coverage_rule}</p>
    </section>
  )
}

function CoverageChip({ group, answer }: { group: AnswerGroup; answer: Answer }) {
  const strong = group.coverage === 'strong'
  return (
    <a
      href={`#${anchor(group)}`}
      data-coverage={group.coverage}
      title={`${group.name}: ${countLine(group)}`}
      className={`inline-flex items-center gap-1.5 border px-2 py-[3px] font-mono text-[10.5px] leading-[1.4] hover:border-line-strong ${
        strong ? 'border-accent-graph/60 text-text' : 'border-dashed border-accent-attention/70 text-text-muted'
      }`}
    >
      <span className="font-sans text-[12px]">{group.name}</span>
      <span className="tabular-nums">{group.sources}</span>
      <span className={strong ? 'text-accent-graph' : 'text-accent-attention'}>{coverageWord(group, answer)}</span>
    </a>
  )
}

/**
 * What the unplaced group holds, in a reader's words: sources about no country, sources about
 * several whose matching passages name none of them (`B-168`), and sources not yet checked.
 */
function unplacedNote(group: AnswerGroup): string {
  const several = group.several_places ?? 0
  const unexamined = group.unexamined
  const none = group.sources - several - unexamined
  const parts: string[] = []
  if (none > 0)
    parts.push(
      `${plural(none, 'source')} that ${none === 1 ? 'names' : 'name'} no country often enough to be about one`,
    )
  if (several > 0)
    parts.push(`${plural(several, 'source')} about several countries, in passages that name none of them`)
  if (unexamined > 0) parts.push(`${plural(unexamined, 'source')} not yet checked for places`)
  if (parts.length === 0) return 'Sources that name no country often enough to be about one.'
  const last = parts.pop()!
  const text = parts.length ? `${parts.join(', ')} and ${last}` : last
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`
}

function GroupSection({
  group,
  answer,
  question,
  topic,
  rated = group.code !== null,
  onReadPlace,
  onReadAll,
}: {
  group: AnswerGroup
  answer: Answer
  question: string
  topic: string | null
  /** Whether the coverage rule is stated for it: a country, or the one-list answer. */
  rated?: boolean
  onReadPlace?: (code: string) => void
  onReadAll?: () => void
}) {
  const placed = group.code !== null
  const thin = rated && group.coverage === 'thin'
  const more = group.sources - group.items.length
  const readAll = placed && onReadPlace ? () => onReadPlace(group.code!) : rated ? onReadAll : undefined
  return (
    <section id={anchor(group)} aria-label={group.name} className="scroll-mt-20 border border-line bg-surface">
      <header className="flex flex-col gap-1 border-b border-line/60 px-5 py-3.5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h3 className="font-sans text-[16px] font-semibold leading-snug text-text">{group.name}</h3>
          {rated ? (
            <span
              className={`font-mono text-[10px] uppercase tracking-[0.1em] ${
                thin ? 'text-accent-attention' : 'text-accent-graph'
              }`}
            >
              {coverageWord(group, answer)}
            </span>
          ) : null}
        </div>
        <p className="font-mono text-[10.5px] text-text-faint">{countLine(group)}</p>
        {thin ? <p className="text-[12.5px] text-text-muted">{thinReason(group, answer)}</p> : null}
        {!placed && !rated ? <p className="text-[12.5px] text-text-muted">{unplacedNote(group)}</p> : null}
      </header>

      <ol className="divide-y divide-line/60">
        {group.items.map((item) => (
          <ItemRow key={item.source_id} item={item} />
        ))}
      </ol>

      {more > 0 || thin ? (
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-line/60 px-5 py-3">
          {more > 0 && readAll ? (
            // The rest of this country, or of the one list, as the ranked passages of the same
            // search (`B-175`). The unplaced group has no code to narrow by: a plain count.
            <button
              type="button"
              onClick={readAll}
              className="font-mono text-[10.5px] text-accent-graph hover:underline"
            >
              {plural(more, 'more source')}: read all {group.sources} as passages →
            </button>
          ) : (
            <span className="font-mono text-[10.5px] text-text-faint">
              {more > 0 ? `${plural(more, 'more source')} not shown` : ''}
            </span>
          )}
          {thin ? <FindMore question={question} place={placed ? group.name : ''} topic={topic} /> : null}
        </footer>
      ) : null}
    </section>
  )
}

function ItemRow({ item }: { item: AnswerItem }) {
  const href = hrefForSource(item.source_id, item.chunk_id)
  return (
    <li className="flex flex-col gap-1.5 px-5 py-3.5">
      <a
        href={href}
        onClick={onInternalClick(href)}
        className="min-w-0 break-words font-sans text-[14px] font-semibold leading-snug text-text hover:text-accent-graph"
      >
        {item.title ?? item.publisher}
      </a>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <TierChip tier={item.source_tier} />
        <span className="font-mono text-[10px] text-text-faint">{item.publication_date ?? 'no date'}</span>
        <span className="break-all font-mono text-[10px] text-text-faint">{item.publisher}</span>
        {item.passages > 1 ? (
          <span className="font-mono text-[10px] text-text-faint">{item.passages} matching passages</span>
        ) : null}
      </div>
      {/* The source's own words, clamped; the whole passage is on its page. */}
      <p className="line-clamp-4 whitespace-pre-line text-[13px] leading-[1.55] text-text/85">{readable(item.text)}</p>
    </li>
  )
}

// --------------------------------------------------------------------------
// Find more
// --------------------------------------------------------------------------

type Sent =
  | { phase: 'idle' }
  | { phase: 'sending' }
  | { phase: 'queued'; already: boolean }
  | { phase: 'failed'; message: string }

function useFindMore(question: string, topic: string | null) {
  const [sent, setSent] = useState<Sent>({ phase: 'idle' })
  function send(place: string) {
    setSent({ phase: 'sending' })
    queueFindMore(question, place, topic)
      .then(() => setSent({ phase: 'queued', already: false }))
      .catch((cause: unknown) => {
        // 409: the same search is already waiting — the reader's intent is met.
        if (cause instanceof ApiError && cause.status === 409) setSent({ phase: 'queued', already: true })
        else
          setSent({
            phase: 'failed',
            message: cause instanceof ApiError ? cause.message : 'The search was not queued.',
          })
      })
  }
  return { sent, send }
}

function SentLine({ sent }: { sent: Sent }) {
  if (sent.phase === 'queued') {
    return (
      <p role="status" className="font-mono text-[10.5px] text-accent-graph">
        {sent.already ? 'Already queued' : 'Queued'} — results arrive as the crawler fetches them.
      </p>
    )
  }
  if (sent.phase === 'failed') {
    return (
      <p role="alert" className="font-mono text-[10.5px] text-accent-attention">
        Not queued: {sent.message}
      </p>
    )
  }
  return null
}

export function FindMore({ question, place, topic }: { question: string; place: string; topic: string | null }) {
  const { sent, send } = useFindMore(question, topic)
  if (sent.phase === 'queued') return <SentLine sent={sent} />
  return (
    <div className="flex flex-wrap items-center gap-2">
      <SentLine sent={sent} />
      <button
        type="button"
        className={BUTTON_SECONDARY}
        disabled={sent.phase === 'sending'}
        onClick={() => send(place)}
        title={`Queues a web search for “${findMoreQuery(question, place)}”${topic ? `, filed under ${topic}` : ''}`}
      >
        {place ? `Find more about ${place}` : 'Find more on this question'}
      </button>
    </div>
  )
}

function FindMoreAnywhere({ question, topic }: { question: string; topic: string | null }) {
  const [place, setPlace] = useState('')
  const { sent, send } = useFindMore(question, topic)
  return (
    <form
      className="flex flex-col gap-2 border border-line bg-surface px-5 py-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (place.trim()) send(place.trim())
      }}
    >
      <label htmlFor="find-more-place" className="text-[12.5px] text-text-muted">
        A country missing, or not well-sourced? Queue a search for more about it.
      </label>
      <div className="flex flex-wrap gap-2">
        <input
          id="find-more-place"
          className={`${FIELD} min-w-0 flex-1 basis-48`}
          placeholder="Country name"
          value={place}
          onChange={(event) => setPlace(event.target.value)}
        />
        <button type="submit" className={BUTTON_SECONDARY} disabled={sent.phase === 'sending' || !place.trim()}>
          Find more
        </button>
      </div>
      <SentLine sent={sent} />
    </form>
  )
}
