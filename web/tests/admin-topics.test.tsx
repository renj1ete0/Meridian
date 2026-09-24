/**
 * Steering (task P6-12, spec §10, §10.1).
 *
 * The design problem this screen has, and the reason most of these tests are
 * about copy: **the number you set is not always the number that applies.** A
 * floor lifts a starved topic, a ceiling caps a dominant one, a boost multiplies
 * until it expires, and a paused topic leaves the pool entirely while keeping
 * its weight.
 *
 * A screen that showed only the stored weight would be wrong exactly when
 * something interesting was happening, and silently. So the share is shown
 * beside the weight, and whenever they disagree the row says which mechanism
 * did it — with a note that appears *only* then, because a note on every row is
 * noise and then the rows that need one stop standing out.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { delta, reweightLines } from '../src/admin/Reweight'
import {
  SteeringRail,
  auditGroups,
  auditLine,
  runCost,
  runOutcome,
} from '../src/admin/SteeringRail'
import { STATUSES, TopicPanel, percent, shareNote } from '../src/admin/TopicPanel'
import type { RunRow, SteeringEntry, TopicConfig, TopicRow } from '../src/lib/api'

const REPO = join(fileURLToPath(new URL('..', import.meta.url)), '..')

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function config(over: Partial<TopicConfig> = {}): TopicConfig {
  return {
    topic: 'walkability',
    weight: 0.4,
    floor: 0.05,
    ceiling: 0.6,
    boost_factor: null,
    boost_expires_at: null,
    pinned: false,
    status: 'active',
    description: null,
    ...over,
  }
}

function row(over: Partial<TopicRow> = {}): TopicRow {
  return {
    topic: config(over.topic),
    effective_weight: 0.4,
    share: 0.4,
    boost_active: false,
    ...over,
  }
}

// --------------------------------------------------------------------------
// The share and the weight
// --------------------------------------------------------------------------

describe('a row says what it draws and what it stores', () => {
  it('shows both numbers', () => {
    // The stored weight on the row, the share drawn beneath it. They differ
    // exactly when something interesting is happening.
    const rendered = text(
      renderToStaticMarkup(
        <TopicPanel rows={[row({ share: 0.339, topic: config({ weight: 0.4 }) })]} sumsTo={1} />,
      ),
    )

    expect(rendered).toContain('walkability 0.40')
    expect(rendered).toContain('draws 33.9% of seeds')
  })

  it('publishes what the pool adds up to', () => {
    // One number, and the whole screen rests on it. Assuming it would make a
    // vector that had drifted look exactly like one that had not.
    const rendered = text(renderToStaticMarkup(<TopicPanel rows={[row()]} sumsTo={0.97} />))

    expect(rendered).toContain('Normalised 0.97')
  })

  it('keeps one decimal, so a floor and a near-floor are different', () => {
    // Rounded to whole numbers, 5.4% reads as exactly at the 5% floor — and
    // "at its floor" is a distinct state with a distinct explanation.
    expect(percent(0.054)).toBe('5.4%')
    expect(percent(0.05)).toBe('5.0%')
  })
})

describe('the note appears only when the share is not the weight', () => {
  it('says nothing on an ordinary row', () => {
    // The property that makes the notes below worth reading. A note on every
    // row is noise, and then the rows that need one do not stand out.
    expect(shareNote(row())).toBeNull()
  })

  it('explains a topic lifted to its floor', () => {
    const note = shareNote(row({ share: 0.05, topic: config({ floor: 0.05, weight: 0.001 }) }))

    expect(note).toContain('5.0%')
    expect(note).toContain('stall')
  })

  it('explains a topic held at its ceiling', () => {
    const note = shareNote(row({ share: 0.6, topic: config({ ceiling: 0.6, weight: 0.9 }) }))

    expect(note).toContain('ceiling')
    expect(note).toContain('60.0%')
  })

  it('explains a boost as temporary', () => {
    // §10's whole promise for this mode is that you do not have to remember to
    // undo it. A note calling it a boost without saying it returns would leave
    // a reader with exactly the doubt the mechanism exists to remove.
    const note = shareNote(row({ boost_active: true, share: 0.6 }))

    expect(note).toContain('expires')
  })

  it('says a paused topic keeps its weight rather than losing it', () => {
    // §10.2: nothing is deleted, so returning costs nothing. A reader who
    // thinks pausing discards the weight will not pause.
    const note = shareNote(row({ topic: config({ status: 'paused' }), share: 0 }))

    expect(note).toContain('costs nothing')
  })

  it('does not call a ceiling of 1.0 a ceiling', () => {
    // An unbounded topic reaching 100% is not being held anywhere, and saying
    // it is would be a mechanism reported where none is acting.
    expect(shareNote(row({ share: 1, topic: config({ ceiling: 1 }) }))).toBeNull()
  })
})

// --------------------------------------------------------------------------
// §10.2 — archive is not delete
// --------------------------------------------------------------------------

describe('the copy does not describe a system that deletes', () => {
  it('says so at the top', () => {
    const rendered = text(renderToStaticMarkup(<TopicPanel rows={[row()]} sumsTo={1} />))

    expect(rendered).toContain('Nothing here deletes')
  })

  it('gives every status a hint that says what it does to the pool', () => {
    for (const status of STATUSES) {
      expect(status.hint.length, `${status.key} has no hint`).toBeGreaterThan(10)
    }
  })

  it('never uses the word delete or permanent about archiving', () => {
    const archived = STATUSES.find((s) => s.key === 'archived')!

    expect(archived.hint.toLowerCase()).not.toContain('delete')
    expect(archived.hint.toLowerCase()).not.toContain('permanent')
  })
})

// --------------------------------------------------------------------------
// The audit log (§10.1)
// --------------------------------------------------------------------------

function entry(over: Partial<SteeringEntry> = {}): SteeringEntry {
  return {
    log_id: 1,
    changed_at: '2026-09-15T10:00:00Z',
    actor: 'user',
    topic: 'robotics',
    field: 'weight',
    old_value: '0.150',
    new_value: '0.127',
    reason: "changed through admin: ['weight']",
    ...over,
  }
}

describe('the steering audit explains weights nobody touched', () => {
  it('says that is what it is for, and shows the consequence', () => {
    const rendered = text(renderToStaticMarkup(<SteeringRail entries={[entry()]} runs={[]} />))

    expect(rendered).toContain('steering a different topic')
    expect(rendered).toContain('robotics 0.15 → 0.13')
  })

  it('keeps a third decimal when two would make a change look like none', () => {
    // 0.401 → 0.404 printed at two places is "0.40 → 0.40": a log line that
    // says nothing moved, about a row that exists because something did.
    expect(auditLine(entry({ old_value: '0.401', new_value: '0.404' }))).toBe(
      'robotics 0.401 → 0.404',
    )
  })

  it('groups the rows one change wrote, and keeps separate changes apart', () => {
    // A single request writes one row per topic it moved, at one instant.
    const same = '2026-09-15T10:00:00Z'
    const groups = auditGroups([
      entry({
        log_id: 3,
        topic: 'walkability',
        old_value: '0.35',
        new_value: '0.40',
        changed_at: same,
      }),
      entry({
        log_id: 2,
        topic: 'robotics',
        old_value: '0.20',
        new_value: '0.15',
        changed_at: same,
      }),
      entry({
        log_id: 1,
        field: 'pinned',
        old_value: 'False',
        new_value: 'True',
        changed_at: '2026-09-14T08:00:00Z',
      }),
    ])

    expect(groups.map((g) => g.lines)).toEqual([
      ['walkability 0.35 → 0.40', 'robotics 0.20 → 0.15'],
      ['robotics pinned'],
    ])
  })

  it('shows a reason somebody wrote, and not the one the server filled in', () => {
    const typed = auditGroups([entry({ reason: 'fetch problem on a portal' })])
    const filled = auditGroups([entry()])

    expect(typed[0]!.reason).toBe('fetch problem on a portal')
    expect(filled[0]!.reason).toBeNull()
  })

  it('names the other writer when it was not a person', () => {
    const rendered = text(
      renderToStaticMarkup(<SteeringRail entries={[entry({ actor: 'orchestrator' })]} runs={[]} />),
    )

    expect(rendered).toContain('orchestrator')
  })

  it('has a line for every field the steering layer logs', () => {
    // Drift: a field `steering.py` records and this file has no case for
    // falls through to a generic line, which is the one that reads worst.
    const source = readFileSync(
      join(REPO, 'packages/meridian_core/meridian_core/steering.py'),
      'utf8',
    )
    const fields = new Set([
      ...[...source.matchAll(/field="([a-z_]+)"/g)].map((m) => m[1]!),
      ...[...source.matchAll(/\("(boost_[a-z_]+)", /g)].map((m) => m[1]!),
      ...[...source.matchAll(/BOUNDS = \(([^)]*)\)/g)].flatMap((m) =>
        [...m[1]!.matchAll(/"([a-z_]+)"/g)].map((f) => f[1]!),
      ),
    ])
    expect(fields.size).toBeGreaterThanOrEqual(5)
    for (const field of fields) {
      const line = auditLine(entry({ field, old_value: '1', new_value: '2' }))
      expect(line, `no line for ${field}`).not.toContain(`${field} 1 → 2`)
    }
  })

  it('says so when nothing has been steered, rather than drawing an empty list', () => {
    const rendered = text(renderToStaticMarkup(<SteeringRail entries={[]} runs={[]} />))

    expect(rendered).toContain('Nothing has been steered yet')
    expect(rendered).toContain('No synthesis run yet')
  })
})

describe('the last runs, beside the steering', () => {
  function runRow(over: Partial<RunRow> = {}): RunRow {
    return {
      run_id: 7,
      started_at: '2026-09-06T06:12:00Z',
      completed_at: null,
      stage: 'done',
      status: 'done',
      agent_id: null,
      tokens_used: 0,
      cost_usd: null,
      edges_added: 318,
      tags_added: 0,
      seeds_emitted: 0,
      last_chunk_id: null,
      heartbeat_at: null,
      error: null,
      ...over,
    }
  }

  it('reports what a finished run wrote, and what stopped one that did not finish', () => {
    expect(runOutcome(runRow())).toBe('+318 edges')
    expect(runOutcome(runRow({ status: 'deferred' }))).toBe('deferred')
  })

  it('never prints a cost nobody measured as zero', () => {
    expect(runCost(runRow())).toBe('—')
    expect(runCost(runRow({ tokens_used: 18915 }))).toBe('18.9k tok')
    expect(runCost(runRow({ cost_usd: 0.84 }))).toBe('$0.84')
  })

  it('carries the newest stop reason in full as the note', () => {
    const reason =
      "every agent for 'tag_attributes' refused: waiting for an answer in the relay directory"
    const rendered = text(
      renderToStaticMarkup(
        <SteeringRail
          entries={[]}
          runs={[runRow({ run_id: 44, status: 'deferred', stage: 'tag', error: reason })]}
        />,
      ),
    )

    expect(rendered).toContain('Run 44 deferred at tag')
    expect(rendered).toContain('in the relay directory')
  })
})

// --------------------------------------------------------------------------
// The arithmetic before committing (design-system §8)
// --------------------------------------------------------------------------

describe('the re-normalisation preview', () => {
  const before = [
    row({ topic: config({ topic: 'walkability', weight: 0.4 }) }),
    row({ topic: config({ topic: 'robotics', weight: 0.35, pinned: true }) }),
    row({ topic: config({ topic: 'biology', weight: 0.25 }) }),
    row({ topic: config({ topic: 'paused-one', weight: 0.2, status: 'paused' }) }),
  ]

  it('names a new topic new, and lists no topic outside the pool', () => {
    const lines = reweightLines(
      before,
      {
        rows: [
          row({ topic: config({ topic: 'walkability', weight: 0.32 }) }),
          row({ topic: config({ topic: 'robotics', weight: 0.35, pinned: true }) }),
          row({ topic: config({ topic: 'biology', weight: 0.2 }) }),
          row({ topic: config({ topic: 'kerbside', weight: 0.13 }) }),
          before[3]!,
        ],
        sums_to: 1,
      },
      'kerbside',
    )

    expect(lines.map((l) => [l.topic, l.change])).toEqual([
      ['walkability', '−0.08'],
      ['robotics', 'pinned, held'],
      ['biology', '−0.05'],
      ['kerbside', 'new'],
    ])
    expect(lines.find((l) => l.topic === 'kerbside')!.focus).toBe(true)
  })

  it('does not call a pinned topic held when the server moved it', () => {
    // The server re-normalises pinned topics too. "pinned, held" beside a
    // number that changed would be the dialog lying on exactly the row
    // somebody pinned to protect.
    const [, pinned] = reweightLines(
      before,
      {
        rows: [
          row({ topic: config({ topic: 'walkability', weight: 0.35 }) }),
          row({ topic: config({ topic: 'robotics', weight: 0.3, pinned: true }) }),
          row({ topic: config({ topic: 'biology', weight: 0.35 }) }),
        ],
        sums_to: 1,
      },
      'biology',
    )

    expect(pinned!.change).toBe('−0.05 · pinned')
  })

  it('says an archived topic releases its weight', () => {
    const lines = reweightLines(
      before,
      {
        rows: [
          row({ topic: config({ topic: 'walkability', weight: 0.53 }) }),
          row({ topic: config({ topic: 'robotics', weight: 0.47, pinned: true }) }),
          row({ topic: config({ topic: 'biology', weight: 0.25, status: 'archived' }) }),
        ],
        sums_to: 1,
      },
      'biology',
    )

    expect(lines.find((l) => l.topic === 'biology')!.change).toBe('released')
  })

  it('uses a real minus sign, so a column of changes lines up', () => {
    expect(delta(-0.08)).toBe('−0.08')
    expect(delta(0.04)).toBe('+0.04')
    expect(delta(0.001)).toBe('0.00')
  })
})

// --------------------------------------------------------------------------
// Cross-language drift
// --------------------------------------------------------------------------

describe('the status list matches the database', () => {
  it('offers exactly the four statuses the CHECK constraint allows', () => {
    // A status added in Postgres and not here cannot be selected; one removed
    // there and left here is an option the database refuses after the click.
    const source = readFileSync(
      join(REPO, 'packages/meridian_core/meridian_core/models/config.py'),
      'utf8',
    )
    const call = /TOPIC_STATUS = constrained\(([\s\S]*?)\)/.exec(source)
    expect(call, 'TOPIC_STATUS is no longer written the way this test reads it').toBeTruthy()

    const declared = [...call![1]!.matchAll(/"([a-z_]+)"/g)]
      .map((m) => m[1]!)
      .filter((value) => value !== 'topic_status')

    expect([...declared].sort()).toEqual(STATUSES.map((s) => s.key).sort())
  })
})
