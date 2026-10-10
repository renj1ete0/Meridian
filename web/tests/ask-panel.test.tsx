// @vitest-environment jsdom
/**
 * "Ask the graph" (tasks P6-06, P6-07).
 *
 * The model is never reached: `fetch` is stubbed with the API's answers. What
 * is tested is what the reader relies on — only checked citations become links,
 * the selection travels as context and can be removed, a follow-up stays in its
 * thread, and a model that could not answer says why where the answer would be.
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AskContextProvider, AskPanel, EARLIER_SHOWN, useAskSubjects } from '../src/explore/AskPanel'
import { answerParts, type ChatExchange, type ChatMessage } from '../src/lib/chat'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const T = '2026-09-27T10:00:00Z'

function message(over: Partial<ChatMessage>): ChatMessage {
  return {
    message_id: 1,
    thread_id: 7,
    role: 'assistant',
    text: '',
    context_entity_ids: null,
    citations: null,
    nodes: null,
    agent_id: 'local-chat',
    model: 'qwen-local',
    input_tokens: 900,
    output_tokens: 100,
    error: null,
    created_at: T,
    ...over,
  }
}

const citation = {
  n: 1,
  chunk_id: 101,
  source_id: 11,
  url: 'https://a.test/doc',
  title: 'A study',
  source_tier: 'peer_reviewed',
}

function exchange(answer: Partial<ChatMessage>, threadId = 7): ChatExchange {
  return {
    thread: { thread_id: threadId, title: 'q', created_at: T, updated_at: T },
    question: message({ message_id: 10, role: 'user', text: 'How does this relate?', thread_id: threadId }),
    answer: message({ message_id: 11, thread_id: threadId, ...answer }),
  }
}

function api(routes: Record<string, unknown>) {
  const calls: { url: string; body?: unknown }[] = []
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), body: init?.body ? JSON.parse(String(init.body)) : undefined })
    const key = Object.keys(routes).find((k) => String(url).includes(k))
    const value = key ? routes[key] : { threads: [], total: 0 }
    return new Response(JSON.stringify(typeof value === 'function' ? (value as () => unknown)() : value), {
      status: 200,
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

function Bound({ subjects }: { subjects: { entityId: number; name: string }[] }) {
  useAskSubjects(subjects)
  return null
}

function renderPanel(subjects: { entityId: number; name: string }[] = []) {
  return render(
    <AskContextProvider>
      <Bound subjects={subjects} />
      <AskPanel />
    </AskContextProvider>,
  )
}

async function ask(text: string) {
  fireEvent.change(screen.getByLabelText('Your question'), { target: { value: text } })
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'Ask' }))
  })
}

describe('answer text and its citations', () => {
  it('links only the citations the server kept', () => {
    const parts = answerParts('It cut speeds [1] and [2].', [citation])
    expect(parts.filter((p) => p.kind === 'cite')).toHaveLength(1)
    expect(parts.map((p) => (p.kind === 'text' ? p.text : `<${p.citation.n}>`)).join('')).toBe(
      'It cut speeds<1> and [2].',
    )
  })

  it('attaches a marker to the word before it, so a line never starts with one', () => {
    const parts = answerParts('Speeds fell  [1], then rose [1].', [citation])
    expect(parts.map((p) => (p.kind === 'text' ? p.text : `<${p.citation.n}>`)).join('')).toBe(
      'Speeds fell<1>, then rose<1>.',
    )
  })

  it('is plain text with nothing cited', () => {
    expect(answerParts('No passage answers this.', null)).toEqual([{ kind: 'text', text: 'No passage answers this.' }])
  })
})

describe('the panel', () => {
  it('opens from the toggle, asks with the selection as context, and shows a cited answer', async () => {
    const calls = api({
      '/chat/ask': exchange({
        text: 'Zones slow buses [1].',
        citations: [citation],
        nodes: [
          { ref: 1, entity_id: 501, name: 'Silver Zone', contested: false },
          { ref: 2, entity_id: 502, name: 'Modal shift claim', contested: true },
        ],
      }),
    })
    renderPanel([{ entityId: 501, name: 'Silver Zone' }])
    fireEvent.click(screen.getByRole('button', { name: 'Ask the graph' }))
    const panel = screen.getByRole('complementary', { name: 'Ask the graph' })
    expect(within(panel).getByText('Context')).toBeTruthy()

    await ask('How does this relate?')

    const posted = calls.find((c) => c.url.endsWith('/api/admin/chat/ask'))!
    expect(posted.body).toEqual({ question: 'How does this relate?', thread_id: null, context_entity_ids: [501] })
    const link = await screen.findByRole('link', { name: '[1]' })
    expect(link.getAttribute('href')).toBe('/sources/11?passage=101')
    const chips = within(screen.getByRole('list', { name: 'Nodes cited' })).getAllByRole('link')
    expect(chips.map((c) => c.getAttribute('href'))).toEqual(['/nodes/501', '/nodes/502'])
    expect(chips[1]!.textContent).toContain('†')
    expect(panel.textContent).toContain('1 contested')
  })

  it('keeps a follow-up in its thread', async () => {
    const calls = api({ '/chat/ask': () => exchange({ text: 'Answer.' }, 42) })
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Ask the graph' }))
    await ask('First?')
    await screen.findAllByText('Answer.')
    await ask('And then?')
    const asks = calls
      .filter((c) => c.url.endsWith('/chat/ask'))
      .map((c) => (c.body as { thread_id: number | null }).thread_id)
    expect(asks).toEqual([null, 42])
  })

  it('does not send a context chip the reader removed', async () => {
    const calls = api({ '/chat/ask': exchange({ text: 'ok' }) })
    renderPanel([
      { entityId: 501, name: 'Silver Zone' },
      { entityId: 9, name: 'Kerb extension' },
    ])
    fireEvent.click(screen.getByRole('button', { name: 'Ask the graph' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove Silver Zone from the context' }))
    await ask('Why?')
    expect(
      (calls.find((c) => c.url.endsWith('/chat/ask'))!.body as { context_entity_ids: number[] }).context_entity_ids,
    ).toEqual([9])
  })

  it('shows why a model did not answer, where the answer would be', async () => {
    api({
      '/chat/ask': exchange({
        text: '',
        error: 'No model is set up to answer questions. Enable an agent for `chat` in Admin → Agents.',
      }),
    })
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Ask the graph' }))
    await ask('Anything?')
    expect(await screen.findByText(/No model is set up/)).toBeTruthy()
  })

  it('lists earlier questions, opens one, and offers the rest', async () => {
    const threads = Array.from({ length: EARLIER_SHOWN + 2 }, (_, i) => ({
      thread_id: 100 + i,
      title: `Question ${i}`,
      created_at: T,
      updated_at: T,
    }))
    api({
      '/chat/threads/100': {
        thread: threads[0],
        messages: [
          message({ role: 'user', text: 'Question 0', message_id: 1 }),
          message({ text: 'Earlier answer.', message_id: 2 }),
        ],
      },
      '/chat/threads?': { threads, total: threads.length },
    })
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Ask the graph' }))
    await screen.findByText('Question 0')
    expect(screen.queryByText(`Question ${EARLIER_SHOWN}`)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: `Show all ${threads.length} →` }))
    expect(screen.getByText(`Question ${EARLIER_SHOWN}`)).toBeTruthy()
    fireEvent.click(screen.getByText('Question 0'))
    await waitFor(() => expect(screen.getByText('Earlier answer.')).toBeTruthy())
  })

  it('has no unread badge: the toggle is the glyph alone', () => {
    api({})
    renderPanel()
    expect(screen.getByRole('button', { name: 'Ask the graph' }).textContent).toBe('')
  })
})

describe('the button steps aside on a phone while the reader scrolls down (B-156)', () => {
  function media(narrow: boolean) {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: narrow && query.includes('max-width'), media: query }))
  }

  function scrollTo(y: number) {
    const page = document.scrollingElement ?? document.documentElement
    Object.defineProperty(page, 'scrollTop', { value: y, configurable: true })
    act(() => {
      document.dispatchEvent(new Event('scroll'))
    })
  }

  afterEach(() => vi.unstubAllGlobals())

  it('tucks away going down and comes back going up, and at the top', () => {
    media(true)
    api({})
    renderPanel()
    const button = screen.getByRole('button', { name: 'Ask the graph' })
    expect(button.getAttribute('data-tucked')).toBe('false')

    scrollTo(300)
    expect(button.getAttribute('data-tucked')).toBe('true')
    expect(button.className).toContain('pointer-events-none')
    expect(button.getAttribute('tabindex')).toBe('-1')

    scrollTo(200)
    expect(button.getAttribute('data-tucked')).toBe('false')

    scrollTo(600)
    scrollTo(0)
    expect(button.getAttribute('data-tucked')).toBe('false')
  })

  it('never moves on a wide screen, where it sits beside the content', () => {
    media(false)
    api({})
    renderPanel()
    scrollTo(300)
    expect(screen.getByRole('button', { name: 'Ask the graph' }).getAttribute('data-tucked')).toBe('false')
  })
})

describe('with no model set up (B-183)', () => {
  it('says so before a question is written, and sends the question to Find instead', async () => {
    const calls = api({ '/api/explore/chat/status': { available: false } })
    window.history.pushState({}, '', '/map')
    renderPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Ask the graph' }))
    expect(await screen.findByText(/No model is set up here to answer in prose/)).toBeTruthy()
    expect(screen.queryByText(/citations checked/)).toBeNull()

    fireEvent.change(screen.getByRole('textbox', { name: 'Your question' }), {
      target: { value: 'what slows buses?' },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Find it' }))
    })
    // Nothing was asked of a model that is not there.
    expect(calls.some((c) => c.url.includes('/api/admin/chat/ask'))).toBe(false)
    expect(window.location.pathname).toBe('/')
    const params = new URLSearchParams(window.location.search)
    expect(params.get('q')).toBe('what slows buses?')
    expect(params.get('view')).toBe('answer')
    window.history.pushState({}, '', '/')
  })

  it('asks as before when a model is set up, or when the status cannot be read', async () => {
    // An answer without the field is a status that could not be read: ask as before, and let
    // the server's own refusal speak if it comes to that.
    for (const status of [{ available: true }, {}]) {
      cleanup()
      const calls = api({
        '/api/explore/chat/status': status,
        '/api/admin/chat/ask': exchange({ text: 'It cut speeds.' }),
      })
      renderPanel()
      fireEvent.click(screen.getByRole('button', { name: 'Ask the graph' }))
      await waitFor(() => expect(calls.some((c) => c.url.includes('/chat/status'))).toBe(true))
      expect(screen.getByRole('button', { name: 'Ask' })).toBeTruthy()
      expect(screen.queryByText(/No model is set up here/)).toBeNull()
    }
  })
})
