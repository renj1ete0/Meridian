/**
 * The display's view of passage text: link syntax becomes the words a reader
 * would have seen on the page, and nothing a reader would have seen is lost.
 */
import { describe, expect, it } from 'vitest'

import { flattenTables, plainLetters, readable, unwrapLines } from '../src/lib/readable'

describe('readable', () => {
  it('keeps a link’s words and drops its address', () => {
    expect(readable('See [the 2019 study](https://example.org/a?b=c) for details.')).toBe(
      'See the 2019 study for details.',
    )
  })

  it('turns a footnote link into a plain marker', () => {
    expect(readable('car-free zones in Vienna [[31](https://link.springer.com#ref-CR31)], Amsterdam')).toBe(
      'car-free zones in Vienna [31], Amsterdam',
    )
  })

  it('handles an address with brackets in it', () => {
    expect(readable('[Bus](https://en.wikipedia.org/wiki/Bus_(disambiguation)) route')).toBe('Bus route')
  })

  it('drops images, including their alt text', () => {
    expect(readable('Before ![a chart of ridership](https://x.org/c.png "Ridership") after')).toBe('Before after')
  })

  it('unwraps an autolink and bold, and removes heading marks', () => {
    expect(readable('## Findings\n**Fares** fell, see <https://x.org/r>')).toBe(
      'Findings\nFares fell, see https://x.org/r',
    )
  })

  it('leaves plain text, brackets and arithmetic alone', () => {
    const plain = 'Ridership rose 12% [see Table 3] (2019); 2 * 3 * 4 = 24 and a_b_c stays.\nitem # 3 is not a heading'
    expect(readable(plain)).toBe(plain)
  })

  it('takes a link whose words wrapped onto a second line only when the address is plainly one', () => {
    expect(readable('implement [Regulatory\nReforms](https://x.org/tdm53.htm) to encourage')).toBe(
      'implement Regulatory\nReforms to encourage',
    )
    const notAddress = 'a [label\nsplit](x) b'
    expect(readable(notAddress)).toBe(notAddress)
    const threeLines = 'a [one\ntwo\nthree](https://x.org) b'
    expect(readable(threeLines)).toBe(threeLines)
  })

  it('reads a footnote whose marker the converter escaped', () => {
    expect(readable('liabilities.[\\[47\\]](https://files.example.gov#_ftn47) For example')).toBe(
      'liabilities.[47] For example',
    )
    expect(readable('[\\[4\\]](https://files.example.gov#fnref4)Over the past year')).toBe('[4]Over the past year')
    expect(readable('a photograph \\[PDF, 10 KB\\] here')).toBe('a photograph [PDF, 10 KB] here')
  })

  it('removes a link whose words were empty, leaving no stray brackets', () => {
    expect(readable('text [](https://x.org/anchor) more')).toBe('text more')
  })

  it('keeps the words of a link the passage begins inside, and drops its address', () => {
    expect(readable('(2019)](https://arxiv.org#bib.bib41) modeled feeders')).toBe('(2019) modeled feeders')
    expect(readable('Evidence from a survey](https://x.org/p/57.html)\nby A. Author')).toBe(
      'Evidence from a survey\nby A. Author',
    )
  })

  it('keeps the words of a link the passage ends inside', () => {
    expect(readable('as the [annual report](https://example.org/rep')).toBe('as the annual report')
  })

  it('leaves a bracket and parenthesis that are not a cut link alone', () => {
    for (const plain of ['a] (b) c', 'see 3](not a url) here', 'list [1](2', 'x [y](z) w']) {
      expect(readable(plain)).toBe(plain === 'x [y](z) w' ? 'x y w' : plain)
    }
  })

  it('does not take a cut link from the middle of a passage', () => {
    const mid = 'first line\nsecond part](https://x.org) and more'
    expect(readable(mid)).toBe(mid)
  })

  it('never lengthens the text', () => {
    for (const sample of ['[a](b)', '**x**', '# h', 'plain', '![i](j)', '<https://a.b>', 'a](#b)', '[a](/b']) {
      expect(readable(sample).length).toBeLessThanOrEqual(sample.length)
    }
  })
})

describe('a page’s own prompts at the start of a passage (B-98)', () => {
  it('drops a short leading line that ends in an ellipsis', () => {
    expect(readable("I'm looking for…\nAutonomous Vehicle Regulations\nSince 2013 the DMV has…")).toBe(
      'Autonomous Vehicle Regulations\nSince 2013 the DMV has…',
    )
  })

  it('drops several such lines, three dots or one character', () => {
    expect(readable('Search...\nMenu…\nThe study found a rise.')).toBe('The study found a rise.')
  })

  it('keeps a long first line that happens to trail off', () => {
    const long = 'Walkable streets were associated with more daily steps in every cohort…\nNext line.'
    expect(readable(long)).toBe(long)
  })

  it('keeps an ellipsis anywhere but the start, and a passage that is only a prompt', () => {
    expect(readable('Intro.\nMore…\nEnd.')).toBe('Intro.\nMore…\nEnd.')
    expect(readable('Loading…')).toBe('Loading…')
  })
})

describe('text a PDF layout broke into lines', () => {
  const wrapped = [
    'The free AFLEET suite of tools',
    'simplifies the task of estimating',
    'petroleum use, greenhouse gas',
    'emissions and cost of ownership.',
    'A second paragraph starts here and',
    'runs on.',
  ].join('\n')

  it('joins lines broken mid-sentence into running text', () => {
    expect(readable(wrapped)).toBe(
      'The free AFLEET suite of tools simplifies the task of estimating petroleum use, greenhouse gas emissions and cost of ownership.\nA second paragraph starts here and runs on.',
    )
  })

  it('joins a line that ends on a word no sentence ends with, even before a capital', () => {
    const text =
      'The Suite of\nTools, developed by researchers at\nArgonne National Laboratory for the\nU.S. Department of Energy (DOE)\nClean Cities Network'
    expect(readable(text)).toBe(
      'The Suite of Tools, developed by researchers at Argonne National Laboratory for the U.S. Department of Energy (DOE)\nClean Cities Network',
    )
  })

  it('does not run a heading or a label into the sentence below it', () => {
    // A navigation page's lines: most breaks mid-sentence, but each next line
    // is capitalised — a new item, not a continuation.
    const menu =
      'Request Data\nRequest Mediation of Disputes\nIf a request is rejected, you may\nsubmit an application\nNews'
    expect(readable(menu)).toBe(
      'Request Data\nRequest Mediation of Disputes\nIf a request is rejected, you may submit an application\nNews',
    )
  })

  it('puts a word hyphenated across the break back together', () => {
    expect(unwrapLines('the trans-\nport network and\nthe bus service and\nthe rail line')).toBe(
      'the transport network and the bus service and the rail line',
    )
  })

  it('leaves a page whose lines were broken on purpose alone', () => {
    // Most breaks end a sentence or a label: not a wrapped passage.
    const labelled = 'Source: LTA.\nDaily bus ridership rose.\nIt fell in 2020.\nIt recovered.'
    expect(readable(labelled)).toBe(labelled)
  })

  it('keeps list items and blank-line paragraphs on their own lines', () => {
    const text =
      'Three measures were\nintroduced in the plan\nwhich covered\n- lower speed limits\n- kerb extensions\n\nThe next part'
    expect(readable(text)).toBe(
      'Three measures were introduced in the plan which covered\n- lower speed limits\n- kerb extensions\n\nThe next part',
    )
  })

  it('does nothing to a passage with too few breaks to judge', () => {
    expect(unwrapLines('one line\nand another')).toBe('one line\nand another')
  })
})

describe('Markdown tables in a passage', () => {
  it('drops the separator row and joins each row’s cells', () => {
    const table = 'Average daily ridership\n| Number (’000) | 2017 | 2018 |\n|---|---|---|\n| Bus | 3,939 | 4,000 |'
    expect(flattenTables(table)).toBe('Average daily ridership\nNumber (’000) · 2017 · 2018\nBus · 3,939 · 4,000')
    expect(readable(table)).not.toMatch(/\||---/)
  })

  it('keeps each row on its own line inside a wrapped passage, and drops a bare pipe line', () => {
    const text =
      '|\n| 2008 | Age | Included |\n| 2008 | Age | Excluded |\nwhich the survey\nreported and\nthe office kept'
    expect(readable(text)).toBe(
      '2008 · Age · Included\n2008 · Age · Excluded\nwhich the survey reported and the office kept',
    )
  })

  it('handles aligned separators and leaves a lone pipe in prose alone', () => {
    expect(flattenTables('| a | b |\n| :-- | --: |')).toBe('a · b')
    expect(flattenTables('either this | or that')).toBe('either this | or that')
  })
})

describe('plainLetters', () => {
  // A journal title as one PDF's small-caps font handed it over: letters as U+F7xx.
  const smallCaps = 'T J  L U'

  it('turns private-use small capitals back into the letters they draw', () => {
    expect(plainLetters(smallCaps)).toBe('The Journal of Land Use')
    expect(plainLetters('V. 9')).toBe('Vol. 9')
  })

  it('leaves every other character alone, including other private-use code points', () => {
    const icons = 'menu  and  stay; café, naïve, 東京 too'
    expect(plainLetters(icons)).toBe(icons)
  })

  it('is part of what a reader sees in a passage', () => {
    expect(readable(`From ${smallCaps}.`)).toBe('From The Journal of Land Use.')
  })
})
