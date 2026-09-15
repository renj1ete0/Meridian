/**
 * The typed client over `/api/explore/*` (task P2-13).
 *
 * The substance of this file is the cross-language drift test. TypeScript types
 * are erased at runtime, so nothing can compare an `interface` to a pydantic
 * model directly — and an API client whose types have quietly diverged from the
 * server is worse than one with no types at all, because the compiler now
 * asserts a shape that is wrong.
 *
 * The chain has two links:
 *
 * 1. `tsc` ties each interface to its `*_FIELDS` list, via the
 *    `Expect<Equal<...>>` assertions in `api.ts`. Verified by breaking it: a
 *    field added to the interface alone fails the build with TS2344.
 * 2. This file ties each `*_FIELDS` list to the pydantic class it mirrors, read
 *    out of `meridian_core/schemas/`.
 *
 * Neither link can be dropped without something going red, so the TypeScript
 * type follows the DTO transitively. Same device as the `SOURCE_TIER` test in
 * `ui.test.tsx`.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  ApiError,
  CHUNK_FIELDS,
  CORPUS_STATS_FIELDS,
  SEARCH_HIT_FIELDS,
  SEARCH_RESPONSE_FIELDS,
  SOURCE_CHUNKS_FIELDS,
  SOURCE_FIELDS,
  corpusStats,
  describeDetail,
  searchCorpus,
  searchQuery,
} from '../src/lib/api'

const REPO = join(fileURLToPath(new URL('..', import.meta.url)), '..')
const SCHEMAS = join(REPO, 'packages/meridian_core/meridian_core/schemas')

/**
 * Field names declared on one pydantic class.
 *
 * Docstrings are stripped before scanning. A prose line inside one can begin
 * with four spaces and a word followed by a colon, and a parser that counted it
 * as a field would fail this test for a reason that has nothing to do with the
 * schema — which is the kind of flake that gets a drift test deleted.
 */
function pydanticFields(file: string, className: string): string[] {
  const source = readFileSync(join(SCHEMAS, file), 'utf8')
  const start = source.indexOf(`class ${className}(`)
  if (start === -1) throw new Error(`${file} has no class ${className}`)

  const rest = source.slice(start)
  const end = rest.slice(1).search(/^class /m)
  const body = (end === -1 ? rest : rest.slice(0, end + 1)).replace(/"""[\s\S]*?"""/g, '')

  return [...body.matchAll(/^ {4}([a-z_][a-z0-9_]*)\s*:/gm)].map((m) => m[1]!)
}

describe('the client types match the DTOs across the language boundary', () => {
  const pairs = [
    ['SearchHitRead', 'search.py', SEARCH_HIT_FIELDS],
    ['SearchResponse', 'search.py', SEARCH_RESPONSE_FIELDS],
    ['CorpusStatsRead', 'search.py', CORPUS_STATS_FIELDS],
    ['SourceChunksRead', 'search.py', SOURCE_CHUNKS_FIELDS],
    ['SourceRead', 'source.py', SOURCE_FIELDS],
    ['ChunkRead', 'source.py', CHUNK_FIELDS],
  ] as const

  it('parses real field names out of the schemas', () => {
    // Guard on the parse. A regex that silently matched nothing would make
    // every assertion below vacuously true, which is the usual way a drift test
    // stops testing anything while still passing.
    const fields = pydanticFields('search.py', 'SearchHitRead')
    expect(fields.length).toBeGreaterThan(5)
    expect(fields).toContain('chunk_id')
    expect(fields).not.toContain('model_config')
  })

  it.each(pairs)('%s', (className, file, declared) => {
    expect([...declared].sort()).toEqual(pydanticFields(file, className).sort())
  })
})

describe('no environment-specific base URL exists', () => {
  const source = readFileSync(join(REPO, 'web/src/lib/api.ts'), 'utf8')
  const code = source.replace(/\/\*\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')

  it('issues only relative paths', () => {
    // §P2-11's reason: a base URL is a value that has to be right per
    // environment, and the way that fails is a production build pointing at
    // somebody's laptop.
    expect(code).not.toMatch(/https?:\/\//)
  })

  it('reads no environment variable', () => {
    expect(code).not.toContain('import.meta.env')
    expect(code).not.toContain('process.env')
  })
})

describe('the query string', () => {
  it('repeats a key per list value rather than joining them', () => {
    // FastAPI's `list[SourceTier] | None` expects repeated keys. A comma-joined
    // value arrives as one tier literally named "government,press" and is
    // rejected as an unknown literal — a 422 that reads like the tier list is
    // wrong rather than the encoding.
    const query = searchQuery({ q: 'x', source_tier: ['government', 'press'] })

    expect(query).toContain('source_tier=government')
    expect(query).toContain('source_tier=press')
    expect(query).not.toContain('government%2Cpress')
  })

  it('omits a filter that is present but empty', () => {
    // "No tier filter" and "a filter matching no tiers" are different requests,
    // and a cleared filter control means the first.
    expect(searchQuery({ q: 'x', source_tier: [] })).not.toContain('source_tier')
  })

  it('omits absent scalars rather than sending undefined', () => {
    const query = searchQuery({ q: 'x' })
    expect(query).not.toContain('undefined')
    expect(query).toBe('q=x')
  })

  it('sends false explicitly, because it is a value and not an absence', () => {
    // `include_duplicates=false` is the default, but a caller that set it
    // deliberately has said something. Dropping falsey values is the bug where
    // an explicit "no" becomes "unspecified".
    expect(searchQuery({ q: 'x', include_duplicates: false })).toContain(
      'include_duplicates=false',
    )
  })

  it('encodes a query that would otherwise break the URL', () => {
    expect(searchQuery({ q: 'a&b=c' })).toBe('q=a%26b%3Dc')
  })
})

describe('errors name their cause', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('passes through a string detail', () => {
    expect(describeDetail('offset 500 reaches past the candidate pool', 422)).toContain('offset 500')
  })

  it('renders a validation array as readable text, not [object Object]', () => {
    // FastAPI's `detail` is a string for a raised HTTPException and an array of
    // per-field objects for a validation failure. Rendering the second directly
    // gives "[object Object]", which §4 would call an error naming neither its
    // cause nor its remedy.
    const message = describeDetail(
      [{ loc: ['query', 'source_tier', 0], msg: "Input should be 'government'" }],
      422,
    )

    expect(message).not.toContain('[object Object]')
    expect(message).toContain('source_tier')
    expect(message).toContain("Input should be 'government'")
  })

  it('falls back to the status when the body says nothing', () => {
    expect(describeDetail(undefined, 503)).toContain('503')
  })

  it('distinguishes unreachable from refused', async () => {
    // Different conditions wanting different responses from the reader: one
    // means start the API, the other means fix the request. A single "request
    // failed" hides which.
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))

    await expect(corpusStats()).rejects.toMatchObject({ status: 0 })
    await expect(corpusStats()).rejects.toThrow(/unreachable/i)
  })

  it('surfaces a refusal with its status', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ detail: 'no source 999999' }),
      }),
    )

    const error = await searchCorpus({ q: 'x' }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
    expect((error as ApiError).message).toBe('no source 999999')
  })

  it('survives an error body that is not JSON', async () => {
    // A proxy returning an HTML 502 is the ordinary case here, and a client
    // that throws while parsing the error reports the parse failure instead of
    // the outage.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new SyntaxError('unexpected <')
        },
      }),
    )

    await expect(corpusStats()).rejects.toThrow(/502/)
  })

  it('lets an abort through rather than reporting it as unreachable', async () => {
    // A superseded search is not a failure, and showing "the API is
    // unreachable" because the reader typed another character would be a lie.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError')),
    )

    await expect(corpusStats()).rejects.toThrow(DOMException)
  })
})

describe('dates', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('stay strings, so a citation keeps the day the document published', async () => {
    // `new Date("2025-12-03")` is UTC midnight, which renders as the 2nd in any
    // negative offset. A date here is a fact from a document, not a moment.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ hits: [{ publication_date: '2025-12-03' }] }),
      }),
    )

    const result = await searchCorpus({ q: 'x' })
    expect(result.hits[0]!.publication_date).toBe('2025-12-03')
  })
})
