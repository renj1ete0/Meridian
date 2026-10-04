/**
 * MCP tokens in Admin (task `B-146`, ADRs 0003 and 0011). Mirrors
 * `meridian_core/schemas/tokens.py`; `tests/api.test.ts` holds the field lists to it.
 */
import { request } from './api'

type Equal<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false
type Expect<T extends true> = T

/** Mirrors `TokenRowRead`. Never carries a secret. */
export interface TokenRow {
  token_id: number
  held_by: string
  profile: string
  tools: string[]
  created_at: string
  expires_at: string | null
  state: 'active' | 'expired' | 'revoked'
  no_expiry: boolean
}
export const TOKEN_ROW_FIELDS = [
  'token_id',
  'held_by',
  'profile',
  'tools',
  'created_at',
  'expires_at',
  'state',
  'no_expiry',
] as const
export type AssertTokenRow = Expect<Equal<keyof TokenRow, (typeof TOKEN_ROW_FIELDS)[number]>>

/** Mirrors `TokensRead`. */
export interface Tokens {
  rows: TokenRow[]
  profiles: Record<string, string[]>
  public_url: string | null
}
export const TOKENS_FIELDS = ['rows', 'profiles', 'public_url'] as const
export type AssertTokens = Expect<Equal<keyof Tokens, (typeof TOKENS_FIELDS)[number]>>

/** Mirrors `TokenIssued`: the only response that carries the secret. */
export interface TokenIssued {
  row: TokenRow
  secret: string
}
export const TOKEN_ISSUED_FIELDS = ['row', 'secret'] as const
export type AssertTokenIssued = Expect<Equal<keyof TokenIssued, (typeof TOKEN_ISSUED_FIELDS)[number]>>

export function getTokens(all = false, init?: RequestInit): Promise<Tokens> {
  return request<Tokens>(`/api/admin/tokens${all ? '?all=true' : ''}`, init)
}

export function issueToken(
  body: { held_by: string; profile: string; days: number | null },
  init?: RequestInit,
): Promise<TokenIssued> {
  return request<TokenIssued>('/api/admin/tokens', {
    ...init,
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function revokeToken(tokenId: number, init?: RequestInit): Promise<TokenRow> {
  return request<TokenRow>(`/api/admin/tokens/${tokenId}/revoke`, { ...init, method: 'POST' })
}

/** The MCP address this browser reached the site on: reachable by construction. */
export function mcpUrlFrom(origin: string): string {
  return `${origin.replace(/\/$/, '')}/mcp`
}

/** Whether an address is reachable from beyond this machine and its LAN. */
export function isPublic(url: string): boolean {
  try {
    const { protocol, hostname } = new URL(url)
    const local =
      hostname === 'localhost' ||
      hostname === '127.0.0.1' ||
      /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)/.test(hostname) ||
      hostname.endsWith('.local')
    return protocol === 'https:' && !local
  } catch {
    return false
  }
}

export type Client = 'claude-code' | 'gemini-cli' | 'other'

/** Ready-to-paste setup for a client. */
export function setupFor(client: Client, url: string, secret: string): string {
  if (client === 'claude-code') {
    return `claude mcp add --transport http meridian ${url} \\\n  --header "Authorization: Bearer ${secret}"`
  }
  if (client === 'gemini-cli') {
    return `"mcpServers": {\n  "meridian": {\n    "httpUrl": "${url}",\n    "headers": { "Authorization": "Bearer ${secret}" }\n  }\n}`
  }
  return `URL:    ${url}\nHeader: Authorization: Bearer ${secret}\n\nAny MCP client that speaks streamable HTTP with a custom header.`
}
