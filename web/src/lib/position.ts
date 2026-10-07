import type { PageUnit } from './api'

/**
 * Where a passage sits, in words a reader can cite, or null when there is nothing to cite
 * (`B-156`). A page is shown; a character offset is the corpus's own bookkeeping and is left to
 * the record details, as is a position whose unit was never recorded.
 *
 * @param unit - What `value` counts, from the source's media type.
 * @param value - The passage's `page_or_offset`.
 */
export function citablePosition(unit: PageUnit | null, value: number | null): string | null {
  if (unit !== 'page' || value === null) return null
  return `page ${value}`
}
