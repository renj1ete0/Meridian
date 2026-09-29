/**
 * Passage text as a reader should see it.
 *
 * Pages are converted to Markdown at ingestion, and about a quarter of stored
 * passages still carry its link syntax — `[text](https://…)`, footnote markers
 * like `[[30](https://…#ref-30)]`, images, `**bold**` and `#` headings. The
 * stored text stays as it is: it is the source everything is re-derived from
 * (§2.4), and embedding already reads a view without URLs (`B-49`). This is the
 * display's own view of it, and changes nothing but what is drawn.
 */

const IMAGE = /!\[[^\]\n]*\]\([^)\s]*(?:\s+"[^"]*")?\)/g
// Innermost first, so a footnote `[[30](url)]` becomes `[30]`.
const LINK = /\[([^[\]\n]*)\]\((?:[^()\s]|\([^()\s]*\))*(?:\s+"[^"]*")?\)/g
const AUTOLINK = /<(https?:\/\/[^>\s]+)>/g
const BOLD = /(\*\*|__)(?=\S)([^*_\n]+?)(?<=\S)\1/g
const HEADING = /^[ \t]{0,3}#{1,6}[ \t]+/gm
const EMPTY_BRACKETS = /\[\s*\]/g
// A short line ending in an ellipsis at the very start is a page's own prompt
// ("I'm looking for…", "Search…"), a widget's label, not what the page says
// (`B-98`). Only at the start and only short, so a sentence left unfinished
// mid-passage is kept.
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
  return text
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
 * Text extracted from a PDF carries its column width: a line ends wherever the
 * page did, mid-sentence, three words in. Read as-is it is a ragged column; with
 * every newline kept it cannot even be skimmed. But web pages break lines on
 * purpose (a label, then its value), and joining those would run them together.
 * So a passage counts as wrapped only when most of its line breaks fall
 * mid-sentence, and then only breaks that plainly continue are joined — the
 * next line in lower case, or this one ending on a comma or on a word such as
 * "of" or "the" that no sentence ends with. One after a full
 * stop, before a capitalised line, a list item or a blank line stays. A word hyphenated across the
 * break is put back together.
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
