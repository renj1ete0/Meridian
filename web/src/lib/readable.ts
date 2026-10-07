/**
 * Passage text as a reader should see it: Markdown link syntax, images, emphasis and
 * headings removed, and layout line breaks joined. Display only; the stored text is
 * unchanged. See docs/features/web-app.md#readable-passages.
 */

const IMAGE = /!\[[^\]\n]*\]\([^)\s]*(?:\s+"[^"]*")?\)/g
// Innermost first, so a footnote `[[30](url)]` becomes `[30]`.
const LINK = /\[([^[\]\n]*)\]\((?:[^()\s]|\([^()\s]*\))*(?:\s+"[^"]*")?\)/g
const AUTOLINK = /<(https?:\/\/[^>\s]+)>/g
const BOLD = /(\*\*|__)(?=\S)([^*_\n]+?)(?<=\S)\1/g
const HEADING = /^[ \t]{0,3}#{1,6}[ \t]+/gm
const EMPTY_BRACKETS = /\[\s*\]/g
// A short line ending in an ellipsis at the very start is a widget's prompt ("Search…"),
// not what the page says (`B-98`); mid-passage ellipses are kept.
const LEADING_PROMPTS = /^(?:[^\n]{1,40}(?:…|\.\.\.)[ \t]*\n+)+/

// A Markdown table's separator row: pipes, dashes, colons and spaces only.
const TABLE_RULE = /^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(?:\|[ \t]*:?-{2,}:?[ \t]*)*\|?[ \t]*$/
// A table row: starts and ends with a pipe, with at least one inside.
const TABLE_ROW = /^[ \t]*\|(.*\|.*)\|[ \t]*$/

/**
 * Markdown tables as a reader can read them in running text: the separator row
 * goes, and each row's cells are joined with a middle dot. A passage is an
 * excerpt, so a grid would be cut mid-table anyway; the pipes were noise.
 */
export function flattenTables(text: string): string {
  return (
    text
      .split('\n')
      // The rule row, and a line of nothing but pipes left where a table began.
      .filter((line) => !TABLE_RULE.test(line) && !/^[ \t]*\|[ \t|]*$/.test(line))
      .map((line) => {
        const row = TABLE_ROW.exec(line)
        if (!row) return line
        return row[1]!
          .split('|')
          .map((cell) => cell.trim())
          .filter(Boolean)
          .join(' · ')
      })
      .join('\n')
  )
}

// A line ending on a word that cannot end a sentence: what follows continues
// it, even capitalised ("researchers at / Argonne", "Suite of / Tools").
const JOINING_WORD = /\b(?:of|and|or|the|a|an|to|for|in|at|on|by|with|from|as|that)$/i

// A flattened table row.
const TABLE_CELLS = / · /

// A line that ends a sentence or introduces what follows.
const ENDS_SENTENCE = /[.!?:;"”’)\]]$/
// A line that stands on its own whatever came before: a list item or a heading.
const STANDS_ALONE = /^\s*(?:[-*•▪◦]\s|\d{1,3}[.)]\s|[A-Z][A-Z0-9 &-]{2,}$)/

/**
 * Join lines a PDF's layout broke, and keep the breaks an author meant.
 *
 * Only when most breaks fall mid-sentence, and then only breaks that plainly continue
 * (next line lower case, or this one ending on a comma or a joining word) are joined;
 * hyphenated words are rejoined. See docs/features/web-app.md#readable-passages.
 */
export function unwrapLines(text: string): string {
  const lines = text.split('\n')
  const breaks = lines.slice(0, -1).filter((line, i) => line.trim() && lines[i + 1]!.trim())
  if (breaks.length < 3) return text
  const midSentence = breaks.filter((line) => !ENDS_SENTENCE.test(line.trim())).length
  if (midSentence / breaks.length < 0.5) return text

  let out = lines[0]!
  for (let i = 1; i < lines.length; i++) {
    const prev = lines[i - 1]!.trim()
    const line = lines[i]!
    // Joined only where the break is plainly mid-sentence: the next line goes
    // on in lower case, or this one stopped on a comma. A capitalised next line
    // may be a heading, a label or a table row, and running it into the line
    // above reads worse than the break did.
    const continues = /^\s*[a-z(]/.test(line) || /[,&]$/.test(prev) || JOINING_WORD.test(prev)
    const keep =
      !prev ||
      !line.trim() ||
      !continues ||
      ENDS_SENTENCE.test(prev) ||
      STANDS_ALONE.test(line) ||
      STANDS_ALONE.test(prev) ||
      // A table row `flattenTables` wrote: its cells are joined with ` · `.
      TABLE_CELLS.test(prev)
    if (keep) out += '\n' + line
    else if (/[a-z]-$/.test(prev) && /^\s*[a-z]/.test(line)) out = out.replace(/-\s*$/, '') + line.trim()
    else out = out.replace(/\s+$/, '') + ' ' + line.trim()
  }
  return out
}

export function readable(text: string): string {
  let out = text.replace(IMAGE, '')
  // Links nest one level in footnotes; two passes resolve `[[n](url)]`.
  for (let i = 0; i < 2; i++) out = out.replace(LINK, (_, label: string) => label)
  out = unwrapLines(flattenTables(out))
  return out
    .replace(AUTOLINK, (_, url: string) => url)
    .replace(BOLD, (_, __, inner: string) => inner)
    .replace(HEADING, '')
    .replace(EMPTY_BRACKETS, '')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
    .replace(LEADING_PROMPTS, '')
}
