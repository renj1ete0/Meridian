/**
 * The display's view of passage text: link syntax becomes the words a reader
 * would have seen on the page, and nothing a reader would have seen is lost.
 */
import { describe, expect, it } from 'vitest'

import { readable } from '../src/lib/readable'

describe('readable', () => {
  it('keeps a link’s words and drops its address', () => {
    expect(readable('See [the 2019 study](https://example.org/a?b=c) for details.')).toBe(
      'See the 2019 study for details.',
    )
  })

  it('turns a footnote link into a plain marker', () => {
    expect(
      readable('car-free zones in Vienna [[31](https://link.springer.com#ref-CR31)], Amsterdam'),
    ).toBe('car-free zones in Vienna [31], Amsterdam')
  })

  it('handles an address with brackets in it', () => {
    expect(readable('[Bus](https://en.wikipedia.org/wiki/Bus_(disambiguation)) route')).toBe('Bus route')
  })

  it('drops images, including their alt text', () => {
    expect(readable('Before ![a chart of ridership](https://x.org/c.png "Ridership") after')).toBe('Before after')
  })

  it('unwraps an autolink and bold, and removes heading marks', () => {
    expect(readable('## Findings\n**Fares** fell, see <https://x.org/r>')).toBe('Findings\nFares fell, see https://x.org/r')
  })

  it('leaves plain text, brackets and arithmetic alone', () => {
    const plain = 'Ridership rose 12% [see Table 3] (2019); 2 * 3 * 4 = 24 and a_b_c stays.\nitem # 3 is not a heading'
    expect(readable(plain)).toBe(plain)
  })

  it('does not join a link across lines', () => {
    const broken = 'a [label\nsplit](https://x.org) b'
    expect(readable(broken)).toBe(broken)
  })

  it('removes a link whose words were empty, leaving no stray brackets', () => {
    expect(readable('text [](https://x.org/anchor) more')).toBe('text more')
  })

  it('never lengthens the text', () => {
    for (const sample of ['[a](b)', '**x**', '# h', 'plain', '![i](j)', '<https://a.b>']) {
      expect(readable(sample).length).toBeLessThanOrEqual(sample.length)
    }
  })
})
