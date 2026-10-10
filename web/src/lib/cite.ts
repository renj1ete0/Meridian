/**
 * A passage as a reader pastes it into their own writing (`B-179`): the words, then where they
 * are from, then the way back to this corpus's copy. See docs/features/web-app.md#source-pages.
 */
import type { PageUnit } from './api'
import { readable } from './readable'

/** Past this a quote is cut at a word and marked, as a citation quotes rather than reprints. */
export const QUOTE_MAX = 400

export interface CitedSource {
  source_id: number
  title: string | null
  url: string
  publisher?: string | null
  publication_date: string | null
  page_unit: PageUnit | null
}

export interface CitedPassage {
  chunk_id: number
  text: string
  page_or_offset: number | null
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function quote(text: string): string {
  const plain = readable(text).replace(/\s+/g, ' ').trim()
  if (plain.length <= QUOTE_MAX) return plain
  const cut = plain.slice(0, QUOTE_MAX)
  const atWord = cut.lastIndexOf(' ')
  return `${(atWord > QUOTE_MAX * 0.6 ? cut.slice(0, atWord) : cut).trimEnd()} …`
}

/**
 * The citation text. A page is given only when the source counts pages; a character offset
 * means nothing to a reader of the original. The date is written as the source gives it, or
 * "n.d." when it gives none.
 */
export function passageCitation(source: CitedSource, passage: CitedPassage, origin: string): string {
  // Untitled, the source is named by its publisher or host: the URL follows anyway, and naming
  // it by its URL wrote the address twice.
  const name = source.title ?? (source.publisher || hostOf(source.url))
  const where = [
    name,
    source.publisher && source.publisher !== name ? source.publisher : null,
    source.publication_date ?? 'n.d.',
    source.page_unit === 'page' && passage.page_or_offset !== null ? `p. ${passage.page_or_offset}` : null,
  ].filter(Boolean)
  return [
    `“${quote(passage.text)}”`,
    `— ${where.join(', ').replace(/\.$/, '')}. ${source.url}`,
    `Passage in Meridian: ${origin}/sources/${source.source_id}?passage=${passage.chunk_id}`,
  ].join('\n')
}
