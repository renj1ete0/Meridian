/**
 * Narrowing a search to a topic (task P6-24, spec §12.5).
 *
 * `P2-14` put the labels on every source and the filter on the API. The control
 * is small; the property worth testing is the one that is invisible without it.
 *
 * **A topic filter excludes sources nobody has examined**, correctly — nothing
 * has established that they belong to the topic — and silently. A reader who
 * narrows and sees three results has no way to know the corpus holds three
 * hundred documents that were never looked at. So the control says so, once,
 * and only while narrowing: on every render it is noise, and never is an
 * omission the reader cannot see.
 */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { TopicFilter } from '../src/explore/TopicFilter'

function text(markup: string): string {
  return markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&[a-z]+;/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

const TOPICS = ['walkability', 'on-demand-bus', 'robotics']

describe('the control', () => {
  it('offers every configured topic', () => {
    const rendered = text(renderToStaticMarkup(<TopicFilter topics={TOPICS} active={[]} />))

    for (const topic of TOPICS) expect(rendered).toContain(topic)
  })

  it('offers a way back to the whole corpus', () => {
    const rendered = text(renderToStaticMarkup(<TopicFilter topics={TOPICS} active={['robotics']} />))

    expect(rendered).toContain('every topic')
  })

  it('renders nothing at all when no topics are configured', () => {
    // A control with no options is furniture, and an empty row of chips reads
    // as a failed load.
    expect(renderToStaticMarkup(<TopicFilter topics={[]} active={[]} />)).toBe('')
  })

  it('marks the active topics as pressed', () => {
    const markup = renderToStaticMarkup(
      <TopicFilter topics={TOPICS} active={['walkability']} />,
    )

    expect(markup).toContain('aria-pressed="true"')
  })

  it('marks "every topic" as pressed when nothing is selected', () => {
    // Otherwise the unnarrowed state looks like no state at all, and a reader
    // cannot tell a cleared filter from one that never applied.
    const markup = renderToStaticMarkup(<TopicFilter topics={TOPICS} active={[]} />)
    const every = /<button[^>]*>every topic<\/button>/.exec(markup)

    expect(every?.[0]).toContain('aria-pressed="true"')
  })
})

describe('the caveat about unexamined sources', () => {
  it('appears while narrowing, when there are any', () => {
    const rendered = text(
      renderToStaticMarkup(<TopicFilter topics={TOPICS} active={['robotics']} unexamined />),
    )

    expect(rendered).toContain('before topics were recorded')
    expect(rendered).toContain('can hide material')
  })

  it('stays away when nothing is narrowed', () => {
    // Nothing is being hidden, so saying so would train the reader to skip it.
    const rendered = text(
      renderToStaticMarkup(<TopicFilter topics={TOPICS} active={[]} unexamined />),
    )

    expect(rendered).not.toContain('before topics were recorded')
  })

  it('stays away when every source has been examined', () => {
    // The converse, and what makes the first test mean something: the caveat is
    // a fact about the corpus, not decoration on the filter.
    const rendered = text(
      renderToStaticMarkup(<TopicFilter topics={TOPICS} active={['robotics']} />),
    )

    expect(rendered).not.toContain('before topics were recorded')
  })
})

describe('§2 — colour carries no verdict', () => {
  it('uses the graph accent for selection and nothing else', () => {
    // A topic is not better or worse than another one. The only thing colour
    // says here is "this is on".
    const markup = renderToStaticMarkup(<TopicFilter topics={TOPICS} active={['robotics']} />)

    expect(markup).toContain('accent-graph')
    expect(markup).not.toContain('accent-attention')
  })
})

describe('the same control over places (P2-23)', () => {
  const PLACES = ['JP', 'SG']
  const NAMES = { JP: 'First place', SG: 'Second place' }
  const place = (active: string[], unexamined = false) =>
    renderToStaticMarkup(
      <TopicFilter
        topics={PLACES}
        names={NAMES}
        active={active}
        unexamined={unexamined}
        label="Place"
        every="every place"
        caveat="Not yet examined for places."
      />,
    )

  it('shows names rather than stored codes, and keeps the code in reach', () => {
    const markup = place([])
    const rendered = text(markup)

    expect(rendered).toContain('First place')
    expect(rendered).toContain('Second place')
    expect(rendered).not.toContain('JP')
    // The code is still there for a reader who needs it.
    expect(markup).toContain('title="JP"')
    expect(markup).toContain('aria-label="Narrow by place"')
  })

  it('says its own caveat, not the topic one', () => {
    const rendered = text(place(['JP'], true))

    expect(rendered).toContain('Not yet examined for places.')
    expect(rendered).not.toContain('before topics were recorded')
  })

  it('offers its own way back', () => {
    expect(text(place(['JP']))).toContain('every place')
    expect(text(place(['JP']))).not.toContain('every topic')
  })

  it('falls back to the value when a name is missing', () => {
    const markup = renderToStaticMarkup(
      <TopicFilter topics={['KR']} names={{}} active={[]} label="Place" />,
    )
    expect(text(markup)).toContain('KR')
  })
})
