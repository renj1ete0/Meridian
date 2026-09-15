/**
 * Drift tests for the design tokens (tasks P2-12, P6-17).
 *
 * P2-12 states the invariant: "tokens must stay the single source: no raw hex
 * in className, or the design system and the app drift apart immediately."
 * Nothing enforces that by construction — CSS accepts a pasted hex forever — so
 * it is enforced here, in both directions, and neither direction hardcodes a
 * palette: the expected values are parsed out of `docs/design/design-system.md`,
 * so a legitimate palette change is a one-file edit and an illegitimate one is a
 * failure.
 *
 * Since `P6-17` the token file separates *palettes* (`--dark-*`, `--light-*`,
 * each published colour written once) from *roles* (`--surface`, `--text`, what
 * components actually use), with one mapping block per theme state. That makes a
 * second class of drift possible and therefore testable: a role remapped in one
 * theme block and forgotten in another, which leaves the reader stuck on the
 * previous theme's colour for that one role and nothing else.
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
const appCss = readFileSync(join(WEB, 'src/styles/app.css'), 'utf8')

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

/** The body of one CSS block, by its selector. */
function block(selector: string): string {
  const start = tokensCss.indexOf(`${selector} {`)
  if (start === -1) throw new Error(`tokens.css has no ${selector} block`)
  return tokensCss.slice(start, tokensCss.indexOf('\n  }', start) + 1 || tokensCss.indexOf('\n}', start))
}

/** `--name: #HEX;` declarations in a block. */
function colours(selector: string): Map<string, string> {
  const found = new Map<string, string>()
  for (const match of block(selector).matchAll(/^\s*--([a-z-]+):\s*(#[0-9A-Fa-f]{6});/gm)) {
    found.set(match[1]!, match[2]!.toUpperCase())
  }
  return found
}

/** `--role: var(--something);` remappings in a block. */
function remapped(selector: string): Set<string> {
  const found = new Set<string>()
  for (const match of block(selector).matchAll(/^\s*--([a-z-]+):\s*var\(--[a-z-]+\);/gm)) {
    found.add(match[1]!)
  }
  return found
}

describe('the palette in code matches the palette in the design system', () => {
  it.each([
    ['Dark tokens (flagship)', 'dark'],
    ['Light tokens', 'light'],
  ] as const)('%s', (heading, prefix) => {
    const published = publishedTokens(heading)
    const inCode = colours(':root')

    expect(published.size).toBeGreaterThan(5) // the parse must not silently yield nothing

    for (const [token, hex] of published) {
      expect(inCode.get(`${prefix}-${token}`), `--${prefix}-${token}`).toBe(hex)
    }
  })

  it('defines no colour the design system does not publish', () => {
    const publishedValues = new Set([
      ...publishedTokens('Dark tokens (flagship)').values(),
      ...publishedTokens('Light tokens').values(),
    ])

    for (const [token, hex] of colours(':root')) {
      expect(publishedValues.has(hex), `--${token}: ${hex} is outside the palette`).toBe(true)
    }
  })

  it('writes each published colour exactly once', () => {
    // The reason palettes and roles are separate files-within-a-file. Repeated
    // hexes mean a palette change is an edit in several places with no way to
    // notice when one is missed.
    const literals = [...tokensCss.matchAll(/#[0-9A-Fa-f]{6}/g)].map((m) => m[0].toUpperCase())
    const seen = new Set<string>()
    const repeated = literals.filter((hex) => !seen.add(hex))

    expect(repeated).toEqual([])
  })
})

describe('the theme states remap a consistent set of roles', () => {
  const systemLight = "  :root:not([data-theme='dark'])"

  it('system-light and explicit-light are the same theme', () => {
    // If they diverge, a reader who never touches the toggle and a reader who
    // explicitly picks light see different colours — and only one of them is
    // ever tested by whoever built it.
    expect([...remapped("[data-theme='light']")].sort()).toEqual(
      [...remapped(systemLight)].sort(),
    )
  })

  it('explicit dark can undo everything light remapped', () => {
    // Otherwise switching back to dark on a light-mode machine leaves whichever
    // role light changed and dark forgot stuck on the light value — one wrong
    // colour in an otherwise correct theme, which reads as a rendering glitch.
    const light = remapped("[data-theme='light']")
    const dark = remapped("[data-theme='dark']")
    const stranded = [...light].filter((role) => !dark.has(role))

    expect(stranded).toEqual([])
  })

  it('leaves exactly the canvas roles on their dark values in light', () => {
    // §2's graph-canvas table is headed "Dark" and has no light column, so
    // these are the four the design system does not publish. Pinned so that a
    // role quietly dropped from the light mapping fails rather than inheriting.
    const roles = remapped(':root')
    const light = remapped("[data-theme='light']")

    expect([...roles].filter((role) => !light.has(role)).sort()).toEqual([
      'accent-attention-deep',
      'accent-graph-deep',
      'ground-deep',
    ])
  })

  it('every role is available as a Tailwind utility', () => {
    // Completeness: a role added to tokens.css and not to `@theme` exists but
    // cannot be used, and the component that wants it reaches for a literal.
    const missing = [...remapped(':root')].filter(
      (role) => !appCss.includes(`--color-${role}: var(--${role});`),
    )

    expect(missing).toEqual([])
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
        // The regexes in this very file are the only legitimate hex-shaped
        // strings in the tree; they are patterns, not colours.
        if (/#[0-9A-Fa-f]{3,8}\b/.test(line) && !line.includes('[0-9A-Fa-f]')) {
          offenders.push(`${relative(REPO, path)}:${index + 1}: ${line.trim()}`)
        }
      }
    }
    expect(offenders, 'use a token, not a literal').toEqual([])
  })
})
