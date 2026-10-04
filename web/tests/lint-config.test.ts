/**
 * The web linter (task B-142): each rule the project relies on still fires.
 *
 * A config that parses but no longer applies a rule (a renamed rule, a plugin that stopped
 * loading) fails silently: the linter passes because it checks less. Each case below is a
 * file that breaks one rule, linted with the real config.
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterAll, describe, expect, it } from 'vitest'

const WEB = fileURLToPath(new URL('..', import.meta.url))
const dir = mkdtempSync(join(tmpdir(), 'meridian-lint-'))
afterAll(() => rmSync(dir, { recursive: true, force: true }))

function lint(name: string, source: string): string {
  const file = join(dir, name)
  writeFileSync(file, source)
  try {
    execFileSync(join(WEB, 'node_modules/.bin/oxlint'), ['-c', join(WEB, '.oxlintrc.json'), file], {
      cwd: WEB,
      encoding: 'utf8',
      stdio: 'pipe',
    })
    return ''
  } catch (failure) {
    const { stdout = '', stderr = '' } = failure as { stdout?: string; stderr?: string }
    return stdout + stderr
  }
}

const CASES: [rule: string, file: string, source: string][] = [
  [
    'rules-of-hooks',
    'hooks.tsx',
    `import { useState } from 'react'
export function A({ on }: { on: boolean }) {
  if (on) {
    const [x] = useState(0)
    return x
  }
  return null
}
`,
  ],
  [
    'exhaustive-deps',
    'deps.tsx',
    `import { useEffect, useState } from 'react'
export function A({ id }: { id: number }) {
  const [, set] = useState(0)
  useEffect(() => set(id), [])
  return null
}
`,
  ],
  ['no-unused-vars', 'unused.ts', `const unused = 1\nexport const used = 2\n`],
  ['no-explicit-any', 'any.ts', `export const x: any = 1\n`],
  ['tsdoc', 'doc.ts', '/** A ``reST`` span, which TSDoc reads as empty. */\nexport const x = 1\n'],
]

describe('the lint config', () => {
  it.each(CASES)('reports %s', (rule, file, source) => {
    expect(lint(file, source)).toContain(rule)
  })

  it('passes clean code, so the cases above fail for their rule alone', () => {
    const output = lint('clean.ts', '/** Two numbers added. */\nexport const add = (a: number, b: number) => a + b\n')
    expect(output).toBe('')
  })
})
