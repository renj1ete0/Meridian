/**
 * Properties of `readable()` (`Q-03`): what must hold for any passage, found by fast-check
 * rather than by the cases somebody thought of. It is display only, so it may tidy text but
 * must never invent it, lose plain words, or fail on what a converter produced.
 */
import fc from 'fast-check'
import { describe, expect, it } from 'vitest'

import { readable } from '../src/lib/readable'

const word = fc.stringMatching(/^[A-Za-z]{1,12}$/)
const sentence = fc.array(word, { minLength: 1, maxLength: 12 }).map((ws) => ws.join(' '))

describe('readable(), for any text', () => {
  it('never throws and never makes a passage longer', () => {
    fc.assert(
      fc.property(fc.string({ maxLength: 400 }), (text) => {
        expect(readable(text).length).toBeLessThanOrEqual(text.length)
      }),
      { numRuns: 500 },
    )
  })

  it('leaves plain prose as it was', () => {
    fc.assert(
      fc.property(fc.array(sentence, { minLength: 1, maxLength: 4 }), (sentences) => {
        const text = sentences.map((s) => `${s}.`).join(' ')
        expect(readable(text)).toBe(text)
      }),
    )
  })

  it('keeps a link’s words and drops its address', () => {
    fc.assert(
      fc.property(sentence, sentence, word, (before, label, host) => {
        const text = `${before} [${label}](https://${host}.example/${host}) ends.`
        const out = readable(text)
        expect(out).toContain(label)
        expect(out).not.toContain('](')
        expect(out).not.toContain(`${host}.example`)
      }),
    )
  })

  it('settles: reading the readable text again changes nothing', () => {
    fc.assert(
      fc.property(
        fc.array(
          fc.oneof(
            sentence,
            word.map((w) => `**${w}**`),
            word.map((w) => `[${w}](https://x.example)`),
          ),
          { maxLength: 8 },
        ),
        (parts) => {
          const once = readable(parts.join(' '))
          expect(readable(once)).toBe(once)
        },
      ),
    )
  })
})
