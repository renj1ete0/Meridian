/**
 * The answer page as a brief a reader takes away (`B-206`): the question, where the evidence
 * is and how well-sourced, then its sources quoted with a citation and the way back: by
 * country when the page groups by country, else as the one list (`B-241`).
 * Nothing is generated; every line is the answer page's own. See docs/features/search.md#the-answer-page.
 */
import { coverageWord, type Answer, type AnswerGroup, type AnswerItem } from './answer'
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

function section(group: AnswerGroup, answer: Answer, origin: string, rated = group.code !== null): string {
  const head = rated ? `## ${group.name}: ${coverageWord(group, answer)}` : `## ${group.name}`
  const counts = `${plural(group.sources, 'source')} from ${plural(group.publishers, 'publisher')}`
  const shown = group.items.length < group.sources ? `, ${group.items.length} shown` : ''
  return [head, '', `${counts}${shown}.`, '', ...group.items.map((entry) => item(entry, origin))].join('\n')
}

/** The brief as Markdown. `asOf` is when it was read, written as the reader's day. */
export function answerBrief(answer: Answer, question: string, origin: string, asOf: string): string {
  const strong = answer.groups.filter((g) => g.coverage === 'strong').length
  const listed = answer.grouped_by === 'none' ? answer.ungrouped : null
  const summary = listed
    ? `${plural(listed.sources, 'source')} from ${plural(listed.publishers, 'publisher')}, ${coverageWord(listed, answer)}. `
    : `${plural(answer.groups.length, 'country', 'countries')}, ${strong} well-sourced, ` +
      `from ${plural(answer.sources_considered, 'source')}. `
  const body = listed
    ? [section(listed, answer, origin, true), '']
    : [
        ...answer.groups.map((group) => section(group, answer, origin)).flatMap((s) => [s, '']),
        ...(answer.unplaced ? [section(answer.unplaced, answer, origin), ''] : []),
      ]
  return [
    `# ${question}`,
    '',
    `Evidence from Meridian's corpus, ${asOf}: ${summary}` +
      'Every line below is quoted from a source; nothing is generated.',
    '',
    `> ${answer.grouping_reason}`,
    '',
    `> ${answer.coverage_rule}`,
    ...(answer.degraded && answer.degraded_reason ? ['', `> ${answer.degraded_reason}`] : []),
    '',
    ...body,
    `Read it again: ${origin}/?q=${encodeURIComponent(question)}&view=answer`,
    '',
  ].join('\n')
}
