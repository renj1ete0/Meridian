/**
 * Topic weights, driven (task P6-28, spec §10, §10.2; design-system §8).
 *
 * The behaviours here only exist with a DOM: a drag that stages rather than
 * writes, a menu whose Archive goes through a confirmation instead of straight
 * to a status write, a dialog that will not commit until the server has shown
 * the arithmetic, and a boost form that cannot send one half of a boost.
 * `admin-topics.test.tsx` holds the copy and the pure functions.
 */
// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { BoostsTable, boostExpired, factorOf } from '../src/admin/BoostsTable'
import { AddTopicDialog, PREVIEW_DEBOUNCE_MS, TAKES_EFFECT, bound } from '../src/admin/TopicDialogs'
import { TopicPanel, caption } from '../src/admin/TopicPanel'
import {
  ApiError,
  type SteeringEntry,
  type TopicAddBody,
  type TopicConfig,
  type TopicRow,
} from '../src/lib/api'

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

function entry(over: Partial<SteeringEntry> = {}): SteeringEntry {
  return {
    log_id: 1,
    changed_at: '2026-09-15T10:00:00Z',
    actor: 'user',
    topic: 'robotics',
    field: 'weight',
    old_value: '0.150',
    new_value: '0.127',
    reason: null,
    ...over,
  }
}

describe('topic weights, driven', () => {
  afterEach(cleanup)

  const rows = [
    row({ topic: config({ topic: 'walkability', weight: 0.4 }), share: 0.4 }),
    row({ topic: config({ topic: 'robotics', weight: 0.35, pinned: true }), share: 0.35 }),
    row({ topic: config({ topic: 'biology', weight: 0.25, status: 'paused' }), share: 0 }),
    row({ topic: config({ topic: 'economics', weight: 0.1, status: 'archived' }), share: 0 }),
  ]

  it('marks paused and pinned in form, not only in colour', () => {
    const rendered = text(renderToStaticMarkup(<TopicPanel rows={rows} sumsTo={1} />))

    expect(rendered).toContain('biology Paused')
    expect(rendered).toContain('robotics Pinned')
  })

  it('will not move a weight the server would refuse', () => {
    // `set_weight` refuses a topic outside the pool, and a slider inside its
    // own bounds — the input spans floor to ceiling only.
    render(<TopicPanel rows={rows} sumsTo={1} />)

    const paused = screen.getByLabelText('Weight for biology') as HTMLInputElement
    const active = screen.getByLabelText('Weight for walkability') as HTMLInputElement
    expect(paused.disabled).toBe(true)
    expect(active.disabled).toBe(false)
    expect([active.min, active.max]).toEqual(['5', '60'])
  })

  it('stages a drag as a draft rather than writing it', () => {
    const onDraft = vi.fn()
    render(<TopicPanel rows={rows} sumsTo={1} onDraft={onDraft} />)

    fireEvent.change(screen.getByLabelText('Weight for walkability'), { target: { value: '30' } })

    expect(onDraft).toHaveBeenCalledWith('walkability', 0.3)
    // Apply waits for a draft and for the server's preview of it.
    expect((screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows every row where the preview puts it, and what it was', () => {
    const preview = {
      rows: [
        row({ topic: config({ topic: 'walkability', weight: 0.3 }), share: 0.3 }),
        row({ topic: config({ topic: 'robotics', weight: 0.45, pinned: true }), share: 0.45 }),
        ...rows.slice(2),
      ],
      sums_to: 1,
    }
    render(
      <TopicPanel
        rows={rows}
        sumsTo={1}
        draft={{ topic: 'walkability', weight: 0.3 }}
        preview={preview}
      />,
    )

    const robotics = document.querySelector('[data-topic="robotics"]')!
    expect(robotics.textContent).toContain('was 0.35')
    expect(robotics.textContent).toContain('0.45')
    expect((screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement).disabled).toBe(
      false,
    )
  })

  it('shows the server’s refusal of a draft as written', () => {
    render(
      <TopicPanel
        rows={rows}
        sumsTo={1}
        draft={{ topic: 'walkability', weight: 0.3 }}
        refusal="0.300 is outside 'walkability''s bounds (0.350–0.600)."
      />,
    )

    expect(screen.getByRole('alert').textContent).toContain('outside')
  })

  it('pauses from the menu, and sends archiving through its confirmation', () => {
    const onStatus = vi.fn()
    const onArchive = vi.fn()
    render(<TopicPanel rows={rows} sumsTo={1} onStatus={onStatus} onArchive={onArchive} />)

    fireEvent.click(screen.getByRole('button', { name: 'Actions for walkability' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Pause acquisition' }))
    expect(onStatus).toHaveBeenCalledWith('walkability', 'paused')

    fireEvent.click(screen.getByRole('button', { name: 'Actions for walkability' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /Archive topic/ }))
    // Not a status write: archiving re-normalises the rest, so it goes through
    // the dialog that shows that first.
    expect(onArchive).toHaveBeenCalledWith('walkability')
    expect(onStatus).toHaveBeenCalledTimes(1)
  })

  it('styles archive in the attention accent and in nothing that reads as destruction', () => {
    render(<TopicPanel rows={rows} sumsTo={1} />)
    fireEvent.click(screen.getByRole('button', { name: 'Actions for walkability' }))

    const archive = screen.getByRole('menuitem', { name: /Archive topic/ })
    expect(archive.className).toContain('text-accent-attention')
    expect(archive.className).not.toMatch(/red|danger|destructive/)
  })

  it('offers to resume a paused topic, not to pause it again', () => {
    render(<TopicPanel rows={rows} sumsTo={1} />)
    fireEvent.click(screen.getByRole('button', { name: 'Actions for biology' }))

    expect(screen.getByRole('menuitem', { name: 'Resume acquisition' })).toBeTruthy()
    expect(screen.queryByRole('menuitem', { name: 'Pause acquisition' })).toBeNull()
  })

  it('moves archived topics to their own list, where Restore is one click', () => {
    const onStatus = vi.fn()
    render(<TopicPanel rows={rows} sumsTo={1} onStatus={onStatus} />)

    expect(screen.queryByLabelText('Weight for economics')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Archived · 1/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }))

    expect(onStatus).toHaveBeenCalledWith('economics', 'active')
  })

  it('says a paused topic keeps its weight, and when it stopped', () => {
    const paused = rows[2]!
    const since = caption(paused, [
      entry({
        topic: 'biology',
        field: 'status',
        old_value: 'active',
        new_value: 'paused',
        changed_at: '2026-09-04T21:40:00Z',
      }),
    ])

    expect(since).toBe('weight kept · acquisition suspended 04 Sep')
  })
})

describe('the add-topic dialog', () => {
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  const rows = [
    row({ topic: config({ topic: 'walkability', weight: 0.6 }) }),
    row({ topic: config({ topic: 'robotics', weight: 0.4 }) }),
  ]

  it('asks the server for the arithmetic, and adds only once it has it', async () => {
    vi.useFakeTimers()
    const preview = vi.fn(async (body: TopicAddBody) => ({
      rows: [
        row({ topic: config({ topic: 'walkability', weight: 0.57 }) }),
        row({ topic: config({ topic: 'robotics', weight: 0.38 }) }),
        row({ topic: config({ topic: body.topic, weight: 0.05 }) }),
      ],
      sums_to: 1,
    }))
    const onAdd = vi.fn()
    render(
      <AddTopicDialog rows={rows} sumsTo={1} preview={preview} onAdd={onAdd} onClose={() => {}} />,
    )

    const add = screen.getByRole('button', { name: 'Add topic' }) as HTMLButtonElement
    expect(add.disabled).toBe(true)

    fireEvent.change(screen.getByLabelText('Topic label'), { target: { value: ' kerbside ' } })
    await act(() => vi.advanceTimersByTimeAsync(PREVIEW_DEBOUNCE_MS))

    // The server's defaults, so an untouched dialog previews exactly what an
    // API call with no bounds would do.
    expect(preview).toHaveBeenCalledWith(
      { topic: 'kerbside', floor: 0.05, ceiling: 0.6 },
      expect.anything(),
    )
    expect(document.querySelector('[data-topic="kerbside"]')!.textContent).toContain('new')
    expect(add.disabled).toBe(false)

    fireEvent.click(add)
    expect(onAdd).toHaveBeenCalledWith({ topic: 'kerbside', floor: 0.05, ceiling: 0.6 })
  })

  it('shows the server’s refusal and does not offer to commit it', async () => {
    vi.useFakeTimers()
    const preview = vi.fn(async () => {
      throw new ApiError(422, 'floors sum to 1.050; at most 1.0 can be guaranteed.')
    })
    render(
      <AddTopicDialog
        rows={rows}
        sumsTo={1}
        preview={preview}
        onAdd={() => {}}
        onClose={() => {}}
      />,
    )

    fireEvent.change(screen.getByLabelText('Topic label'), { target: { value: 'kerbside' } })
    fireEvent.change(screen.getByLabelText('Floor'), { target: { value: '0.95' } })
    await act(() => vi.advanceTimersByTimeAsync(PREVIEW_DEBOUNCE_MS))

    expect(screen.getByRole('alert').textContent).toContain('floors sum to')
    expect((screen.getByRole('button', { name: 'Add topic' }) as HTMLButtonElement).disabled).toBe(
      true,
    )
  })

  it('does not ask about a bound that is not a share', async () => {
    vi.useFakeTimers()
    const preview = vi.fn()
    render(
      <AddTopicDialog
        rows={rows}
        sumsTo={1}
        preview={preview}
        onAdd={() => {}}
        onClose={() => {}}
      />,
    )

    fireEvent.change(screen.getByLabelText('Topic label'), { target: { value: 'kerbside' } })
    fireEvent.change(screen.getByLabelText('Ceiling'), { target: { value: '1.4' } })
    await act(() => vi.advanceTimersByTimeAsync(PREVIEW_DEBOUNCE_MS * 2))

    expect(preview).not.toHaveBeenCalled()
    expect(bound('1.4')).toBeNull()
    expect(bound('')).toBeNull()
    expect(bound('0.2')).toBe(0.2)
  })

  it('does not promise a planning pass the system does not have', () => {
    // The mock says "at 23:00". The crawl draws a topic per claim, so a clock
    // time here would be a schedule nobody runs.
    expect(TAKES_EFFECT).not.toMatch(/\d{1,2}:\d{2}/)
  })
})

describe('boosts', () => {
  afterEach(cleanup)

  const now = Date.now()
  const later = new Date(now + 86_400_000 * 3).toISOString()
  const earlier = new Date(now - 86_400_000 * 3).toISOString()
  const rows = [
    row({
      topic: config({ topic: 'walkability', boost_factor: 1.8, boost_expires_at: later }),
      boost_active: true,
    }),
    row({
      topic: config({ topic: 'robotics', boost_factor: 1.4, boost_expires_at: earlier }),
      boost_active: false,
    }),
    row({ topic: config({ topic: 'biology' }) }),
  ]

  it('lists running boosts, and expired ones only where asked', () => {
    const active = text(renderToStaticMarkup(<BoostsTable rows={rows} />))
    const all = text(renderToStaticMarkup(<BoostsTable rows={rows} includeExpired />))

    expect(active).toContain('walkability 1.8×')
    expect(active).not.toContain('robotics')
    expect(all).toContain('expired')
    expect(boostExpired(rows[1]!)).toBe(true)
  })

  it('ends a boost by clearing both halves', () => {
    // §10: a factor without an expiry is a permanent change wearing a
    // temporary one's clothes, and the server refuses one half alone.
    const onBoost = vi.fn()
    render(<BoostsTable rows={rows} onBoost={onBoost} />)

    fireEvent.click(screen.getByRole('button', { name: 'End now' }))

    expect(onBoost).toHaveBeenCalledWith('walkability', {
      boost_factor: null,
      boost_expires_at: null,
    })
  })

  it('adds a boost with a factor and an expiry together', () => {
    const onBoost = vi.fn()
    render(<BoostsTable rows={rows} onBoost={onBoost} />)

    fireEvent.click(screen.getByRole('button', { name: '+ Add boost' }))
    fireEvent.change(screen.getByLabelText('Boost topic'), { target: { value: 'biology' } })
    fireEvent.change(screen.getByLabelText('Boost multiplier'), { target: { value: '1.6' } })
    // Relative to today: a fixed date expired on its own and the form rightly refused it (B-143).
    const day = new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10)
    fireEvent.change(screen.getByLabelText('Boost expires'), { target: { value: day } })
    fireEvent.click(screen.getByRole('button', { name: 'Add boost' }))

    expect(onBoost).toHaveBeenCalledWith('biology', {
      boost_factor: 1.6,
      boost_expires_at: `${day}T00:00:00Z`,
    })
  })

  it('refuses a multiplier that is not a positive number', () => {
    expect(factorOf('0')).toBeNull()
    expect(factorOf('-2')).toBeNull()
    expect(factorOf('x')).toBeNull()
    expect(factorOf('1.5')).toBe(1.5)
  })
})

describe('auditLine for seeds (B-55)', () => {
  it('says a seed was added and withdrawn', async () => {
    const { auditLine } = await import('../src/admin/SteeringRail')
    const base = { log_id: 1, changed_at: '2026-09-24T00:00:00Z', actor: 'user', topic: 'walkability', reason: null }
    expect(auditLine({ ...base, field: 'seed', old_value: null, new_value: 'a query' } as never)).toBe(
      'walkability seed: a query',
    )
    expect(auditLine({ ...base, field: 'seed', old_value: 'a query', new_value: null } as never)).toBe(
      'walkability seed withdrawn: a query',
    )
  })
})

describe('auditLine for watches (P6-35)', () => {
  it('says what is being watched', async () => {
    const { auditLine } = await import('../src/admin/SteeringRail')
    const base = { log_id: 1, changed_at: '2026-09-25T00:00:00Z', actor: 'user', topic: 'walkability', reason: null }
    expect(auditLine({ ...base, field: 'watch', old_value: null, new_value: 'an area' } as never)).toBe(
      'walkability watching: an area',
    )
  })
})
