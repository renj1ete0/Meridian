/**
 * Drift tests for the design tokens (task P2-12).
 *
 * P2-12 states the invariant: "tokens must stay the single source: no raw hex
 * in className, or the design system and the app drift apart immediately."
 * That is a claim nothing enforces by construction — CSS will happily accept a
 * pasted hex forever — so it is enforced here, in both directions:
 *
 *   1. every colour the design system publishes is defined in `tokens.css`,
 *      with the same value (the design system changed, the app did not);
 *   2. no other source file contains a colour literal at all (the app changed,
 *      the design system did not).
 *
 * Neither hardcodes a palette. The expected values are parsed out of
 * `docs/design/design-system.md`, so a legitimate palette change is a one-file
 * edit and an illegitimate one is a failure.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const WEB = fileURLToPath(new URL('..', import.meta.url))
const REPO = join(WEB, '..')
const TOKENS = join(WEB, 'src/styles/tokens.css')

const designSystem = readFileSync(join(REPO, 'docs/design/design-system.md'), 'utf8')
const tokensCss = readFileSync(TOKENS, 'utf8')

/** A published row is `| \`accent.graph\` | \`<hex>\` | 8.9 | role |`. */
function publishedTokens(heading: string): Map<string, string> {
  const section = designSystem.split(`### ${heading}`)[1]
  if (!section) throw new Error(`design-system.md has no "### ${heading}" section`)
  const table = section.split('\n##')[0]!

  const found = new Map<string, string>()
  const row = /^\|\s*`([a-z.]+)`\s*\|\s*`(#[0-9A-Fa-f]{6})`/gm
  for (const match of table.matchAll(row)) {
    found.set(match[1]!.replaceAll('.', '-'), match[2]!.toUpperCase())
  }
  return found
}

function block(selector: string): string {
  const start = tokensCss.indexOf(`${selector} {`)
  if (start === -1) throw new Error(`tokens.css has no ${selector} block`)
  return tokensCss.slice(start, tokensCss.indexOf('\n}', start))
}

/** Colour declarations only — `--x: <hex>;` — for comparing against the palette. */
function declared(selector: string): Map<string, string> {
  const found = new Map<string, string>()
  for (const match of block(selector).matchAll(/^\s*--([a-z-]+):\s*(#[0-9A-Fa-f]{6});/gm)) {
    found.set(match[1]!, match[2]!.toUpperCase())
  }
  return found
}

/** Every custom property a block defines, alias or literal — for role coverage. */
function roles(selector: string): Set<string> {
  const found = new Set<string>()
  for (const match of block(selector).matchAll(/^\s*--([a-z-]+):/gm)) found.add(match[1]!)
  return found
}

describe('the palette in code matches the palette in the design system', () => {
  const cases = [
    ['Dark tokens (flagship)', ':root'],
    ['Light tokens', "[data-theme='light']"],
  ] as const

  it.each(cases)('%s', (heading, selector) => {
    const published = publishedTokens(heading)
    const inCode = declared(selector)

    expect(published.size).toBeGreaterThan(5) // the parse itself must not silently yield nothing

    for (const [token, hex] of published) {
      expect(inCode.get(token), `${selector} --${token}`).toBe(hex)
    }
  })

  it('defines no colour the design system does not publish', () => {
    const publishedValues = new Set(
      [...publishedTokens('Dark tokens (flagship)').values()].concat([
        ...publishedTokens('Light tokens').values(),
      ]),
    )
    const inCode = new Map([...declared(':root'), ...declared("[data-theme='light']")])

    for (const [token, hex] of inCode) {
      expect(publishedValues.has(hex), `--${token}: ${hex} is outside the palette`).toBe(true)
    }
  })

  it('carries every dark role into light, so the theme switch is not a rewrite', () => {
    // Light publishes nine tokens against dark's thirteen. The four it omits
    // must still resolve to something, or `[data-theme="light"]` inherits the
    // dark value by accident rather than by decision — see tokens.css for the
    // mapping and `P6-18` for having it decided.
    const lightRoles = roles("[data-theme='light']")
    const unmapped = [...declared(':root').keys()].filter((role) => !lightRoles.has(role))

    expect(unmapped.sort()).toEqual(['accent-attention-deep', 'accent-graph-deep', 'ground-deep'])
  })
})

describe('no colour literal escapes the token file', () => {
  function sources(dir: string): string[] {
    return readdirSync(dir).flatMap((entry) => {
      const path = join(dir, entry)
      if (statSync(path).isDirectory()) return sources(path)
      return /\.(tsx?|css)$/.test(entry) ? [path] : []
    })
  }

  it('across every source file except tokens.css', () => {
    const offenders: string[] = []
    for (const path of [...sources(join(WEB, 'src')), ...sources(join(WEB, 'tests'))]) {
      if (path === TOKENS) continue
      const body = readFileSync(path, 'utf8')
      for (const [index, line] of body.split('\n').entries()) {
        // The regex in this very file is the one legitimate hex-shaped string
        // in the tree; it is a pattern, not a colour.
        if (/#[0-9A-Fa-f]{3,8}\b/.test(line) && !line.includes('[0-9A-Fa-f]')) {
          offenders.push(`${relative(REPO, path)}:${index + 1}: ${line.trim()}`)
        }
      }
    }
    expect(offenders, 'use a token, not a literal').toEqual([])
  })
})
