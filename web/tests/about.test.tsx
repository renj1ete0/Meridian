// @vitest-environment jsdom
/**
 * The About screen (task P6-40; design `About`).
 *
 * Three things matter here, and none of them is that the page renders:
 *
 * - the build it names is the build that was made — the web's `package.json`
 *   and the root `VERSION` agree, or the page states a version nobody shipped;
 * - the topics and counts are read, and a failed read says so rather than
 *   showing an empty list or zeros that look like an empty corpus;
 * - design-system.md §1's rule that the tagline appears here and nowhere else.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AboutPage, BUILD_VERSION, TAGLINE, corpusLine, topicsProse } from '../src/about/AboutPage'
import { sectionOf } from '../src/App'
import type { CorpusStats } from '../src/lib/api'
import { parseRoute } from '../src/lib/route'
import { Settings } from '../src/ui/TopBar'

// `import.meta.dirname`, not a `URL`: under jsdom the global URL is the DOM's, which refuses `file:`.
const WEB = join(import.meta.dirname, '..')
const REPO = join(WEB, '..')

function stats(over: Partial<CorpusStats> = {}): CorpusStats {
  return {
    as_of: '2026-09-25T00:00:00Z',
    sources: 41643,
    chunks: 357074,
    embedded_chunks: 1,
    duplicate_chunks: 1,
    searchable_chunks: 326458,
    entities: 257,
    edges: 196,
    contested_edges: 0,
    new_sources: null,
    new_chunks: null,
    topics: ['first-topic', 'second'],
    sources_without_topics: 0,
    places: [],
    sources_without_places: 0,
    ...over,
  }
}

function serve(respond: () => Response | Promise<Response>) {
  const fetch = vi.fn(async () => respond())
  vi.stubGlobal('fetch', fetch)
  return fetch
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function row(label: string): string {
  const term = screen.getByText(label, { selector: 'dt' })
  return term.nextElementSibling?.textContent ?? ''
}

describe('the build', () => {
  it('is the version the repository says it is', () => {
    // AGENTS.md: `VERSION` is the single source of truth and package.json
    // mirrors it. The page reads package.json, so a mirror left behind would
    // have the About screen name a build that was never made.
    const root = readFileSync(join(REPO, 'VERSION'), 'utf8').trim()
    const pkg = JSON.parse(readFileSync(join(WEB, 'package.json'), 'utf8')) as { version: string }
    expect(pkg.version).toBe(root)
    expect(BUILD_VERSION).toBe(root)
  })

  it('is on the page', async () => {
    serve(() => new Response(JSON.stringify(stats())))
    render(<AboutPage />)
    await screen.findByText(/41,643 sources/)
    expect(row('Build')).toBe(BUILD_VERSION)
  })
})

describe('what it reads', () => {
  it('asks the read-only stats route, and nothing that writes', async () => {
    const fetch = serve(() => new Response(JSON.stringify(stats())))
    render(<AboutPage />)
    await screen.findByText(/41,643 sources/)
    const urls = fetch.mock.calls.map((call) => String((call as unknown[])[0]))
    expect(urls).toEqual(['/api/explore/stats'])
    for (const call of fetch.mock.calls) {
      const init = (call as unknown[])[1] as RequestInit | undefined
      expect(init?.method ?? 'GET').toBe('GET')
    }
  })

  it('names the configured topics, in prose and as a list', async () => {
    serve(() => new Response(JSON.stringify(stats())))
    render(<AboutPage />)
    await screen.findByText(/41,643 sources/)
    expect(row('Topics')).toBe('first-topic · second')
    expect(screen.getByText(/crawls public sources on first topic and second, and relates/)).toBeTruthy()
    expect(row('Corpus')).toBe('41,643 sources · 326,458 searchable passages · 257 nodes · 196 edges')
  })

  it('says a failed read failed, rather than showing an empty corpus', async () => {
    serve(() => new Response(JSON.stringify({ detail: 'The database is unreachable.' }), { status: 503 }))
    render(<AboutPage />)
    expect(await screen.findByText(/Topics and counts could not be read\. The database is unreachable\./)).toBeTruthy()
    expect(row('Topics')).toBe('unavailable')
    expect(row('Corpus')).toBe('unavailable')
    expect(document.body.textContent).not.toMatch(/\b0 sources\b/)
    expect(document.body.textContent).not.toContain('none configured')
    // The build does not depend on the API, so it survives the failure.
    expect(row('Build')).toBe(BUILD_VERSION)
  })

  it('says "reading" while the answer is on its way, not "unavailable"', () => {
    serve(() => new Promise<Response>(() => {}))
    render(<AboutPage />)
    expect(row('Topics')).toBe('reading…')
    expect(screen.queryByText(/could not be read/)).toBeNull()
  })

  it('tells no topics from an unread list', async () => {
    serve(() => new Response(JSON.stringify(stats({ topics: [] }))))
    render(<AboutPage />)
    await screen.findByText(/41,643 sources/)
    expect(row('Topics')).toBe('none configured')
    expect(screen.getByText(/crawls public sources, and relates/)).toBeTruthy()
  })
})

describe('the prose helpers', () => {
  it('joins topics as a sentence would', () => {
    expect(topicsProse([])).toBeNull()
    expect(topicsProse(['one'])).toBe('one')
    expect(topicsProse(['a-b', 'c'])).toBe('a b and c')
    expect(topicsProse(['a', 'b', 'c_d'])).toBe('a, b and c d')
  })

  it('formats counts with separators', () => {
    expect(corpusLine(stats({ sources: 1234567 }))).toMatch(/^1,234,567 sources/)
  })
})

describe('the tagline (design-system.md §1)', () => {
  it('is the published one', () => {
    const designSystem = readFileSync(join(REPO, 'docs/design/design-system.md'), 'utf8')
    expect(designSystem).toContain(`> **${TAGLINE}**`)
  })

  it('appears on the About screen and nowhere else', () => {
    function sources(dir: string): string[] {
      return readdirSync(dir).flatMap((entry) => {
        const path = join(dir, entry)
        if (statSync(path).isDirectory()) return sources(path)
        return /\.(tsx?|css|html)$/.test(entry) ? [path] : []
      })
    }
    const holders = [...sources(join(WEB, 'src')), join(WEB, 'index.html')]
      .filter((path) => readFileSync(path, 'utf8').includes(TAGLINE))
      .map((path) => relative(WEB, path))
    expect(holders).toEqual(['src/about/AboutPage.tsx'])
  })
})

describe('reaching it', () => {
  it('has a real URL', () => {
    expect(parseRoute('/about')).toEqual({ name: 'about' })
    expect(parseRoute('/about/')).toEqual({ name: 'about' })
    expect(parseRoute('/about/more')).toEqual({ name: 'explore' })
  })

  it('marks no section, because it is not one', () => {
    expect(sectionOf(parseRoute('/about'))).toBeNull()
  })

  it('is linked from Settings, and following the link closes the menu', async () => {
    window.history.replaceState({}, '', '/')
    render(<Settings theme="system" onTheme={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }))
    const link = within(screen.getByRole('dialog', { name: 'Settings' })).getByRole('link', { name: 'About Meridian' })
    expect(link.getAttribute('href')).toBe('/about')
    await act(async () => {
      fireEvent.click(link, { button: 0 })
    })
    expect(window.location.pathname).toBe('/about')
    expect(screen.queryByRole('dialog', { name: 'Settings' })).toBeNull()
  })
})
