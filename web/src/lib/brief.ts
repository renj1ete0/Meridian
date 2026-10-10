/**
 * The answer page as a brief a reader takes away (`B-206`): the question, where the evidence
 * is and how strong, then each country's sources quoted with a citation and the way back.
 * Nothing is generated; every line is the answer page's own. See docs/features/search.md#the-answer-page.
 */
import type { Answer, AnswerGroup, AnswerItem } from './answer'
import { passageCitation } from './cite'
import { TIER_LABEL } from '../ui/Tier'

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString('en')} ${n === 1 ? one : many}`
}

function item(entry: AnswerItem, origin: string): string {
  const cited = passageCitation(
    {
      source_id: entry.source_id,
      title: entry.title,
      url: entry.url,
      publisher: entry.publisher,
      publication_date: entry.publication_date,
      // The answer's items carry no page, so none is claimed.
      page_unit: null,
    },
    { chunk_id: entry.chunk_id, text: entry.text, page_or_offset: null },
    origin,
  )
  const [quote, ...rest] = cited.split('\n')
  const tier = TIER_LABEL[entry.source_tier] ?? entry.source_tier
  return [`- ${quote} (${tier.toLowerCase()})`, ...rest.map((line) => `  ${line}`)].join('\n')
}

function section(group: AnswerGroup, origin: string): string {
  const head = group.code === null ? `## ${group.name}` : `## ${group.name}: ${group.coverage}`
  const counts = `${plural(group.sources, 'source')} from ${plural(group.publishers, 'publisher')}`
  const shown = group.items.length < group.sources ? `, ${group.items.length} shown` : ''
  return [head, '', `${counts}${shown}.`, '', ...group.items.map((entry) => item(entry, origin))].join('\n')
}

/** The brief as Markdown. `asOf` is when it was read, written as the reader's day. */
export function answerBrief(answer: Answer, question: string, origin: string, asOf: string): string {
  const strong = answer.groups.filter((g) => g.coverage === 'strong').length
  return [
    `# ${question}`,
    '',
    `Evidence from Meridian's corpus, ${asOf}: ${plural(answer.groups.length, 'country', 'countries')}, ` +
      `${strong} strong, ${answer.groups.length - strong} thin, from ${plural(answer.sources_considered, 'source')}. ` +
      'Every line below is quoted from a source; nothing is generated.',
    '',
    `> ${answer.coverage_rule}`,
    ...(answer.degraded && answer.degraded_reason ? ['', `> ${answer.degraded_reason}`] : []),
    '',
    ...answer.groups.map((group) => section(group, origin)).flatMap((s) => [s, '']),
    ...(answer.unplaced ? [section(answer.unplaced, origin), ''] : []),
    `Read it again: ${origin}/?q=${encodeURIComponent(question)}&view=answer`,
    '',
  ].join('\n')
}
