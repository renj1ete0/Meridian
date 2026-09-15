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

import { STATUSES, TopicPanel, percent, shareNote } from '../src/admin/TopicPanel'
import type { TopicConfig, TopicRow } from '../src/lib/api'

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
    const rendered = text(renderToStaticMarkup(<TopicPanel rows={[row()]} sumsTo={1} />))

    expect(rendered).toContain('40.0% of seeds')
    expect(rendered).toContain('weight 0.400')
  })

  it('publishes what the pool adds up to', () => {
    // One number, and the whole screen rests on it. Assuming it would make a
    // vector that had drifted look exactly like one that had not.
    const rendered = text(renderToStaticMarkup(<TopicPanel rows={[row()]} sumsTo={1} />))

    expect(rendered).toContain('100.0% allocated')
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

describe('the log explains weights nobody touched', () => {
  it('says that is what it is for', () => {
    const rendered = text(
      renderToStaticMarkup(
        <TopicPanel
          rows={[row()]}
          sumsTo={1}
          entries={[
            {
              log_id: 1,
              changed_at: '2026-09-15T10:00:00Z',
              actor: 'user',
              topic: 'robotics',
              field: 'weight',
              old_value: '0.150',
              new_value: '0.127',
              reason: 'renormalised',
            },
          ]}
        />,
      ),
    )

    expect(rendered).toContain('steering a different topic')
    expect(rendered).toContain('robotics weight 0.150 → 0.127')
  })

  it('is absent rather than empty when there is nothing to show', () => {
    const rendered = text(renderToStaticMarkup(<TopicPanel rows={[row()]} sumsTo={1} />))

    expect(rendered).not.toContain('What changed')
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
