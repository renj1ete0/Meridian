/**
 * The path router (`P6-20`'s predecessor).
 *
 * Small enough to hand-roll and therefore small enough to get subtly wrong in
 * ways that only show up as a link that does nothing. The two properties worth
 * pinning are that URLs are real — a source page has to be linkable, or a
 * corpus insisting everything be checkable has made its own documents
 * unaddressable — and that a modified click is left alone.
 */
// @vitest-environment jsdom
import { describe, expect, it } from 'vitest'

import { hrefForSource, parseRoute } from '../src/lib/route'

describe('parsing', () => {
  it('reads a source id out of the path', () => {
    expect(parseRoute('/sources/1059')).toEqual({ name: 'source', sourceId: 1059 })
  })

  it('tolerates a trailing slash', () => {
    expect(parseRoute('/sources/7/')).toEqual({ name: 'source', sourceId: 7 })
  })

  it('falls back to explore for anything else', () => {
    // Not a 404 screen. There are two routes; an unknown path is a mistyped URL
    // or a stale bookmark, and dropping someone on search is more useful than
    // telling them they are lost.
    for (const path of ['/', '/nonsense', '/sources', '/sources/abc']) {
      expect(parseRoute(path)).toEqual({ name: 'explore' })
    }
  })

  it('refuses a non-numeric id rather than coercing it', () => {
    // `Number('..%2F..')` is NaN, and a NaN id would reach the API as a request
    // for `/sources/NaN`. The route simply does not match.
    expect(parseRoute('/sources/../../etc/passwd')).toEqual({ name: 'explore' })
  })
})

describe('links', () => {
  it('builds a source href that round-trips through the parser', () => {
    // The property that keeps a citation shareable: what the UI writes into an
    // `href`, the router must read back.
    expect(parseRoute(hrefForSource(42))).toEqual({ name: 'source', sourceId: 42 })
  })
})

describe('the admin section (task P6-13)', () => {
  it('matches the section root', () => {
    expect(parseRoute('/admin')).toEqual({ name: 'admin' })
    expect(parseRoute('/admin/')).toEqual({ name: 'admin' })
  })

  it('matches a sub-path, because Admin is several screens', () => {
    // A prefix rather than an exact match. Dropping a reader on `/admin/topics`
    // back onto Explore reads as the link being wrong rather than unbuilt, and
    // §12.6 has half a dozen more screens under here.
    expect(parseRoute('/admin/gazetteer')).toEqual({ name: 'admin' })
  })

  it('does not match a path that merely starts with the same letters', () => {
    // `/administration` is not Admin. A bare `startsWith` would claim it, and
    // the bug only shows up once some other screen takes that name.
    expect(parseRoute('/administration')).toEqual({ name: 'explore' })
  })
})
