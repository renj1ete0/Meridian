/**
 * Per-domain fetch policy (task P6-22, spec §6.4).
 *
 * The one admin screen whose changes reach somebody else's server, so the tests
 * are about legibility rather than controls: an operator has to be able to tell,
 * from the row, **which of three layers** put a domain in the state it is in.
 *
 * What is set here, what it resolves to once the global row and the file
 * defaults merge underneath, and what the crawl learned by watching. Only the
 * first is editable, and the third is an observation rather than a setting —
 * which is why it gets its own wording and its own button. A value a reader
 * cannot find anywhere to change is the failure this screen is most prone to.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { FetchPolicyPanel, SUMMARY_KEYS, isLearnedRender } from '../src/admin/FetchPolicyPanel'
import type { FetchPolicy, FetchPolicyRow } from '../src/lib/api'

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function policy(over: Partial<FetchPolicy> = {}): FetchPolicy {
  return {
    domain: 'example.test',
    settings: null,
    status: 'active',
    note: null,
    consecutive_failures: 0,
    render_js_escalations: 0,
    render_js_learned_at: null,
    seed_allowed: null,
    first_seen_via: null,
    novel_fetches: 0,
    trust_state: 'unscreened',
    clean_fetches: 0,
    trust_decided_at: null,
    trust_decided_by: null,
    trust_reason: null,
    updated_at: null,
    updated_by: null,
    ...over,
  }
}

function row(over: Partial<FetchPolicyRow> = {}): FetchPolicyRow {
  return {
    policy: policy(over.policy),
    resolved: {
      delay_per_domain_ms: 1000,
      concurrency_per_domain: 2,
      timeout_s: 30,
      render_js: 'auto',
      block_private_addresses: true,
      ...over.resolved,
    },
    overridden: [],
    ...over,
  }
}

const COUNTS = { active: 12, paused: 1, blocked: 3 }

function render(rows: FetchPolicyRow[]) {
  return renderToStaticMarkup(<FetchPolicyPanel rows={rows} counts={COUNTS} />)
}

// --------------------------------------------------------------------------
// Three layers
// --------------------------------------------------------------------------

describe('a row says which layer decided what', () => {
  it('marks values set on this row', () => {
    // Without it a reader cannot tell a domain somebody tuned from one
    // inheriting the default — and "everything is slow" and "this site is slow"
    // call for different actions.
    const rendered = text(
      render([row({ overridden: ['delay_per_domain_ms'], resolved: { delay_per_domain_ms: 5000 } })]),
    )

    expect(rendered).toContain('5000*')
  })

  it('leaves inherited values unmarked', () => {
    const rendered = text(render([row()]))

    expect(rendered).toContain('1000')
    expect(rendered).not.toContain('1000*')
  })

  it('shows the values that matter at a glance', () => {
    const rendered = text(render([row()]))

    for (const key of SUMMARY_KEYS) {
      expect(rendered, `${key} is missing`).toContain(key.replace(/_/g, ' '))
    }
  })
})

describe('what the crawl learned is not a setting', () => {
  it('recognises a learned render mode', () => {
    expect(
      isLearnedRender(
        row({
          resolved: { render_js: 'always' },
          policy: policy({ render_js_learned_at: '2026-09-15T00:00:00Z', render_js_escalations: 3 }),
        }),
      ),
    ).toBe(true)
  })

  it('does not call a configured render mode learned', () => {
    // The distinction the whole panel turns on. An operator who set `always`
    // themselves must not be told the crawler worked it out.
    expect(
      isLearnedRender(
        row({ resolved: { render_js: 'always' }, overridden: ['render_js'] }),
      ),
    ).toBe(false)
  })

  it('explains it in words, with the count behind it', () => {
    const rendered = text(
      render([
        row({
          resolved: { render_js: 'always' },
          policy: policy({ render_js_learned_at: '2026-09-15T00:00:00Z', render_js_escalations: 4 }),
        }),
      ]),
    )

    expect(rendered).toContain('worked out')
    expect(rendered).toContain('4 pages in a row')
    expect(rendered).toContain('re-checks on its own')
  })

  it('offers to re-check rather than to change a setting', () => {
    // Clearing an observation and setting a policy are different acts, and a
    // button labelled "set to auto" would be the second one.
    const rendered = text(
      render([
        row({
          resolved: { render_js: 'always' },
          policy: policy({ render_js_learned_at: '2026-09-15T00:00:00Z', render_js_escalations: 3 }),
        }),
      ]),
    )

    expect(rendered).toContain('Check again now')
  })
})

// --------------------------------------------------------------------------
// A blocked domain
// --------------------------------------------------------------------------

describe('a blocked domain says so in words', () => {
  it('names the consequence and the count', () => {
    // §6.4 auto-blocks after consecutive failures, so this appears without
    // anybody choosing it — and a blocked domain produces no sources and no
    // errors, which is exactly why it cannot be left to a status chip.
    const rendered = text(
      render([row({ policy: policy({ status: 'blocked', consecutive_failures: 7 }) })]),
    )

    expect(rendered).toContain('Not being crawled')
    expect(rendered).toContain('7 failures')
  })

  it('offers to put it back', () => {
    const rendered = text(render([row({ policy: policy({ status: 'blocked' }) })]))

    expect(rendered).toContain('Crawl it again')
  })

  it('offers nothing to unblock on a domain that is not blocked', () => {
    expect(text(render([row()]))).not.toContain('Crawl it again')
  })
})

// --------------------------------------------------------------------------
// What this screen cannot do
// --------------------------------------------------------------------------

describe('the guards are absent, and their absence is stated', () => {
  it('says where robots and the address guards are set', () => {
    // Not merely omitted. An operator looking for the robots toggle needs to be
    // told it is a deployment setting, or they will go looking for a permission
    // they do not have.
    const rendered = text(render([row()]))

    expect(rendered).toContain('deployment')
    expect(rendered.toLowerCase()).toContain('robots')
  })

  it('offers no control for a safety guard', () => {
    const markup = render([row()])

    expect(markup).not.toContain('block_private_addresses')
    expect(markup).not.toContain('respect_robots')
  })
})

describe('the counts cover every status', () => {
  it('shows what each filter would hold', () => {
    const rendered = text(render([row()]))

    expect(rendered).toContain('active (12)')
    expect(rendered).toContain('blocked (3)')
    expect(rendered).toContain('all (16)')
  })
})

describe('the default row reads as what it is', () => {
  it('names the global row in words rather than as an asterisk', () => {
    const rendered = text(render([row({ policy: policy({ domain: '*' }) })]))

    expect(rendered).toContain('every domain (default)')
  })
})
