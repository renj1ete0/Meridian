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

export function readable(text: string): string {
  let out = text.replace(IMAGE, '')
  // Links nest one level in footnotes; two passes resolve `[[n](url)]`.
  for (let i = 0; i < 2; i++) out = out.replace(LINK, (_, label: string) => label)
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
