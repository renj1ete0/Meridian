/**
 * "Ask the graph" (tasks P6-06, P6-07): wire types, calls, and how an answer is
 * split into text and citation links.
 *
 * Mirrors `meridian_core/schemas/chat.py`; `tests/api.test.ts` reads the Python
 * to hold the field lists together.
 */
import { request } from './api'

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false
type Expect<T extends true> = T

/** Mirrors `ChatCitationRead`: a passage the answer cites as `[n]`, checked by the server. */
export interface ChatCitation {
  n: number
  chunk_id: number
  source_id: number
  url: string
  title: string | null
  source_tier: string
}

/** Mirrors `ChatNodeRead`: a node the answer names, checked by the server. */
export interface ChatNode {
  ref: number
  entity_id: number
  name: string
  contested: boolean
}

/** Mirrors `ChatMessageRead`. */
export interface ChatMessage {
  message_id: number
  thread_id: number
  role: 'user' | 'assistant'
  text: string
  context_entity_ids: number[] | null
  citations: ChatCitation[] | null
  nodes: ChatNode[] | null
  agent_id: string | null
  model: string | null
  input_tokens: number | null
  output_tokens: number | null
  error: string | null
  created_at: string
}

/** Mirrors `ChatThreadRead`. */
export interface ChatThread {
  thread_id: number
  title: string
  created_at: string
  updated_at: string
}

/** Mirrors `ChatThreadsRead`. */
export interface ChatThreads {
  threads: ChatThread[]
  total: number
}

/** Mirrors `ChatThreadDetailRead`. */
export interface ChatThreadDetail {
  thread: ChatThread
  messages: ChatMessage[]
}

/** Mirrors `ChatExchangeRead`. */
export interface ChatExchange {
  thread: ChatThread
  question: ChatMessage
  answer: ChatMessage
}

export const CHAT_CITATION_FIELDS = ['n', 'chunk_id', 'source_id', 'url', 'title', 'source_tier'] as const
export const CHAT_NODE_FIELDS = ['ref', 'entity_id', 'name', 'contested'] as const
export const CHAT_MESSAGE_FIELDS = [
  'message_id',
  'thread_id',
  'role',
  'text',
  'context_entity_ids',
  'citations',
  'nodes',
  'agent_id',
  'model',
  'input_tokens',
  'output_tokens',
  'error',
  'created_at',
] as const
export const CHAT_THREAD_FIELDS = ['thread_id', 'title', 'created_at', 'updated_at'] as const
export const CHAT_THREADS_FIELDS = ['threads', 'total'] as const
export const CHAT_THREAD_DETAIL_FIELDS = ['thread', 'messages'] as const
export const CHAT_EXCHANGE_FIELDS = ['thread', 'question', 'answer'] as const

export type AssertChatCitation = Expect<Equal<keyof ChatCitation, (typeof CHAT_CITATION_FIELDS)[number]>>
export type AssertChatNode = Expect<Equal<keyof ChatNode, (typeof CHAT_NODE_FIELDS)[number]>>
export type AssertChatMessage = Expect<Equal<keyof ChatMessage, (typeof CHAT_MESSAGE_FIELDS)[number]>>
export type AssertChatThread = Expect<Equal<keyof ChatThread, (typeof CHAT_THREAD_FIELDS)[number]>>
export type AssertChatThreads = Expect<Equal<keyof ChatThreads, (typeof CHAT_THREADS_FIELDS)[number]>>
export type AssertChatThreadDetail = Expect<Equal<keyof ChatThreadDetail, (typeof CHAT_THREAD_DETAIL_FIELDS)[number]>>
export type AssertChatExchange = Expect<Equal<keyof ChatExchange, (typeof CHAT_EXCHANGE_FIELDS)[number]>>

export function askGraph(
  question: string,
  options: { threadId?: number | null; contextEntityIds?: readonly number[] } = {},
  init?: RequestInit,
): Promise<ChatExchange> {
  return request<ChatExchange>('/api/admin/chat/ask', {
    method: 'POST',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    body: JSON.stringify({
      question,
      thread_id: options.threadId ?? null,
      context_entity_ids: [...(options.contextEntityIds ?? [])],
    }),
    ...init,
  })
}

export function getChatThreads(limit = 20, init?: RequestInit): Promise<ChatThreads> {
  return request<ChatThreads>(`/api/explore/chat/threads?limit=${limit}`, init)
}

export function getChatThread(threadId: number, init?: RequestInit): Promise<ChatThreadDetail> {
  return request<ChatThreadDetail>(`/api/explore/chat/threads/${threadId}`, init)
}

/** One piece of an answer: plain text, or a `[n]` the server kept. */
export type AnswerPart = { kind: 'text'; text: string } | { kind: 'cite'; citation: ChatCitation }

/**
 * An answer as text and citation links. Only markers the server returned in
 * `citations` become links; any other bracketed number stays as text, since the
 * server already removed every marker it could not stand behind.
 */
export function answerParts(text: string, citations: readonly ChatCitation[] | null): AnswerPart[] {
  const byN = new Map((citations ?? []).map((c) => [c.n, c]))
  const parts: AnswerPart[] = []
  let last = 0
  for (const match of text.matchAll(/\[(\d{1,3})\]/g)) {
    const citation = byN.get(Number(match[1]))
    if (!citation) continue
    const at = match.index ?? 0
    // "reach [1]," renders as a superscript: the space before it would let the
    // line break there and start the next line with the marker, so it goes.
    const before = text.slice(last, at).replace(/\s+$/, '')
    if (before) parts.push({ kind: 'text', text: before })
    parts.push({ kind: 'cite', citation })
    last = at + match[0].length
  }
  if (last < text.length) parts.push({ kind: 'text', text: text.slice(last) })
  return parts
}
