/**
 * The agent registry and run history (task P6-23, spec §11.3, §11.10, §11.11).
 *
 * `P6-23` was held open on the argument that "an empty screen teaches nothing
 * about what the full one should look like", and the arguments below are what
 * the full one turned out to need. Three of them come from the same afternoon
 * as the first real run.
 *
 * **The failure is between the rows.** Routing picks an agent by task type, so
 * a type no usable row declares is a stage that defers every run while every
 * row on the screen looks correct. That line leads, rather than sitting under
 * the table.
 *
 * **An enabled agent with no key looks exactly like a working one**, until a
 * run defers hours later. The reasons are the server's, because a client
 * deriving its own would eventually show a green light for an agent nothing
 * can reach.
 *
 * **No key is ever rendered** (§11.11). The database is snapshotted off-device,
 * which is the whole reason keys are not in it; a screen that echoed one would
 * undo that from the other end.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { AgentsPanel, rowState, tierLabel } from '../src/admin/AgentsPanel'
import { RunsPanel, STATUS_NOTES, spent, written } from '../src/admin/RunsPanel'
import type { AgentRow, RunRow } from '../src/lib/api'

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function agent(over: Partial<AgentRow> = {}): AgentRow {
  return {
    agent_id: 'hosted-frontier',
    provider: 'anthropic',
    model: 'claude-opus-5',
    task_types: ['relation_extraction'],
    quality_tier: 4,
    cost_tier: 'paid',
    availability: 'always',
    enabled: true,
    fallback_agent_id: null,
    route_order: null,
    endpoint: null,
    api_key_env_var: 'ANTHROPIC_API_KEY',
    key_present: true,
    blocked_by: [],
    ...over,
  }
}

function run(over: Partial<RunRow> = {}): RunRow {
  return {
    run_id: 7,
    started_at: '2026-09-22T09:00:00Z',
    completed_at: '2026-09-22T09:04:00Z',
    stage: 'done',
    status: 'done',
    agent_id: 'hosted-frontier',
    tokens_used: 61234,
    cost_usd: 1.42,
    edges_added: 12,
    tags_added: 4,
    seeds_emitted: 0,
    last_chunk_id: 4120,
    heartbeat_at: null,
    error: null,
    ...over,
  }
}

describe('the registry', () => {
  it('leads with the task types nothing serves', () => {
    const markup = renderToStaticMarkup(
      <AgentsPanel rows={[agent({ enabled: false })]} unserved={['relation_extraction']} />,
    )

    expect(text(markup)).toContain('Nothing serves relation_extraction')
    expect(text(markup)).toContain('A run reaching that stage will defer')
  })

  it('agrees in number when several task types are unserved', () => {
    const markup = renderToStaticMarkup(
      <AgentsPanel rows={[agent({ enabled: false })]} unserved={['chat', 'triage']} />,
    )

    expect(text(markup)).toContain('A run reaching those stages will defer')
  })

  it('says so plainly when every task type is covered', () => {
    const markup = renderToStaticMarkup(<AgentsPanel rows={[agent()]} unserved={[]} />)

    expect(text(markup)).toContain('Every task type has an agent')
  })

  it('reports why a row cannot be routed to, rather than that it is enabled', () => {
    // The case that costs an afternoon: enabled, plausible, unreachable.
    const state = rowState(agent({ enabled: true, blocked_by: ['ANTHROPIC_API_KEY is unset here'] }))

    expect(state.label).toContain('unset here')
    expect(state.tone).toBe('blocked')
  })

  it('calls an unblocked row ready', () => {
    expect(rowState(agent()).tone).toBe('ready')
  })

  it('never renders a key, only whether the variable is set', () => {
    const markup = renderToStaticMarkup(<AgentsPanel rows={[agent({ key_present: false })]} unserved={[]} />)

    expect(text(markup)).toContain('ANTHROPIC_API_KEY')
    expect(text(markup)).toContain('not set here')
    expect(markup).not.toContain('sk-')
  })

  it('shows a placeholder model as written', () => {
    // `P4-15` found the seeded registry shipping `<fill in ...>`. A screen that
    // tidied that away would hide the one thing that says it needs filling in.
    const markup = renderToStaticMarkup(
      <AgentsPanel rows={[agent({ model: '<fill in — dedicated process>' })]} unserved={[]} />,
    )

    expect(text(markup)).toContain('fill in')
  })

  it('says when a row declares no task types at all', () => {
    const markup = renderToStaticMarkup(<AgentsPanel rows={[agent({ task_types: [] })]} unserved={[]} />)

    expect(text(markup)).toContain('declares nothing')
  })

  it('offers the one write, labelled by what it will do', () => {
    const enabled = renderToStaticMarkup(<AgentsPanel rows={[agent()]} unserved={[]} />)
    const disabled = renderToStaticMarkup(<AgentsPanel rows={[agent({ enabled: false })]} unserved={[]} />)

    expect(text(enabled)).toContain('Disable')
    expect(text(disabled)).toContain('Enable')
    expect(text(disabled)).not.toContain('Disable')
  })

  it('explains an empty registry instead of showing nothing', () => {
    const markup = renderToStaticMarkup(<AgentsPanel rows={[]} unserved={[]} />)

    expect(text(markup)).toContain('config/agents.yaml')
  })

  it('treats a missing tier as missing rather than as zero', () => {
    expect(tierLabel(null)).toBe('no tier')
    expect(tierLabel(4)).toBe('tier 4')
  })
})

describe('run history', () => {
  it('answers "is something running" before anything else', () => {
    const markup = renderToStaticMarkup(
      <RunsPanel rows={[run()]} total={3} active={run({ run_id: 9, status: 'running', stage: 'extract' })} />,
    )

    expect(text(markup)).toContain('Run 9 is running at extract')
  })

  it('says nothing is running when nothing is', () => {
    const markup = renderToStaticMarkup(<RunsPanel rows={[run()]} total={1} active={null} />)

    expect(text(markup)).toContain('Nothing is running')
  })

  it('explains that deferred is a retry rather than a failure', () => {
    // §13.4 makes deferral ordinary. Somebody reading it as an error goes
    // looking for an outage that is not there.
    expect(STATUS_NOTES.deferred).toContain('resumes it')
  })

  it('shows the whole reason a run stopped', () => {
    const reason =
      "every agent for 'relation_extraction' refused: hosted-frontier reads its key from ANTHROPIC_API_KEY, which is unset."
    const markup = renderToStaticMarkup(
      <RunsPanel rows={[run({ status: 'deferred', error: reason })]} total={1} active={null} />,
    )

    // Not the first forty characters: the sentence names the condition, and
    // the condition is the actionable part.
    expect(text(markup)).toContain('which is unset')
  })

  it('reports counters rather than a verdict', () => {
    expect(written(run())).toBe('12 edges · 4 tags · 0 seeds')
  })

  it('shows a run that wrote nothing as having written nothing', () => {
    // The common case while stages are unbuilt, and the interesting one after.
    expect(written(run({ edges_added: 0, tags_added: 0, seeds_emitted: 0 }))).toContain('0 edges')
  })

  it('omits a cost nobody recorded rather than printing zero', () => {
    expect(spent(run({ cost_usd: null }))).toBe('61,234 tokens')
    expect(spent(run())).toContain('$1.42')
  })

  it('explains an empty history instead of showing nothing', () => {
    const markup = renderToStaticMarkup(<RunsPanel rows={[]} total={0} active={null} />)

    expect(text(markup)).toContain('daily after that')
  })
})

describe('routing order (B-137)', () => {
  it('shows a row its place in the order', () => {
    const markup = renderToStaticMarkup(
      <AgentsPanel rows={[agent({ agent_id: 'local-llamacpp', route_order: 10 })]} unserved={[]} />,
    )
    expect(text(markup)).toContain('tried 10')
  })

  it('says nothing about order for a row without one', () => {
    const markup = renderToStaticMarkup(<AgentsPanel rows={[agent()]} unserved={[]} />)
    expect(text(markup)).not.toContain('tried')
  })
})
