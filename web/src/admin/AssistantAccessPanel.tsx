import { useCallback, useEffect, useState } from 'react'

import { BUTTON_PRIMARY, BUTTON_SECONDARY, Card, FIELD, LABEL, Loading, PageHeader } from './ui'
import { ApiError } from '../lib/api'
import { stampOf, zoneLabel } from '../lib/time'
import {
  getTokens,
  isPublic,
  issueToken,
  mcpUrlFrom,
  revokeToken,
  setupFor,
  type Client,
  type TokenIssued,
  type Tokens,
} from '../lib/tokens'

/**
 * Admin → Assistant access (task `B-146`, ADRs 0003 and 0011; mock
 * `docs/design/AdminAssistantAccess.dc.html`).
 *
 * Issue a read-only MCP token per device, see it once with setup for each client, and revoke
 * it. The address shown is the one this browser reached the site on, so it works by
 * construction. See docs/guides/connecting-an-assistant.md.
 */

const CLIENTS: readonly { key: Client; label: string }[] = [
  { key: 'claude-code', label: 'Claude Code' },
  { key: 'gemini-cli', label: 'Gemini CLI' },
  { key: 'other', label: 'Other clients' },
]

const EXPIRY: readonly { days: number | null; label: string }[] = [
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 365, label: '1 year' },
  { days: null, label: 'Never' },
]

const when = (iso: string | null) => (iso ? `${stampOf(iso)} ${zoneLabel(iso)}` : 'never')

export interface AssistantAccessPanelProps {
  /** Injected in tests; the page loads and acts through the API itself. */
  origin?: string
  load?: (all: boolean) => Promise<Tokens>
  issue?: typeof issueToken
  revoke?: typeof revokeToken
}

export function AssistantAccessPanel({
  origin = window.location.origin,
  load = getTokens,
  issue = issueToken,
  revoke = revokeToken,
}: AssistantAccessPanelProps) {
  const [tokens, setTokens] = useState<Tokens | null>(null)
  const [showRevoked, setShowRevoked] = useState(false)
  const [heldBy, setHeldBy] = useState('')
  const [profile, setProfile] = useState('reader')
  const [days, setDays] = useState<number | null>(90)
  const [issued, setIssued] = useState<TokenIssued | null>(null)
  const [client, setClient] = useState<Client>('claude-code')
  const [message, setMessage] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setTokens(await load(showRevoked))
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : 'The tokens could not be read.')
    }
  }, [load, showRevoked])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const here = mcpUrlFrom(origin)
  const url = tokens?.public_url && isPublic(tokens.public_url) ? tokens.public_url : here
  const exposed = isPublic(url)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setMessage(null)
    try {
      setIssued(await issue({ held_by: heldBy.trim(), profile, days }))
      setHeldBy('')
      await refresh()
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : 'The token could not be issued.')
    }
  }

  async function onRevoke(tokenId: number) {
    try {
      await revoke(tokenId)
      if (issued?.row.token_id === tokenId) setIssued(null)
      await refresh()
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : 'The token could not be revoked.')
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      setMessage('Copied.')
    } catch {
      setMessage('Copy is not available here; select the text instead.')
    }
  }

  const active = tokens?.rows.filter((r) => r.state === 'active').length ?? 0

  return (
    <section className="flex flex-col gap-5">
      <PageHeader title="Assistant access">
        Let an assistant (Claude, Gemini, any MCP client) answer from the corpus with citations, on its own model. A
        token can read; it can never write. Issue one per device, so one can be revoked without the others.
      </PageHeader>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_516px]">
        <Card className="px-[22px] py-[18px]">
          <div className="flex flex-col gap-3">
            <span className={LABEL}>Where /mcp can be reached</span>
            <div className="flex flex-wrap gap-6">
              <Reach label="This browser's address" value={here} />
              <Reach
                label="Public"
                value={tokens?.public_url ?? 'off · no public URL is configured'}
                muted={!tokens?.public_url}
              />
            </div>
          </div>
        </Card>

        <Card className="px-[22px] py-[18px]">
          <form className="flex flex-col gap-3" onSubmit={submit}>
            <span className={LABEL}>Issue a token</span>
            <div className="flex flex-wrap items-end gap-2.5">
              <label className="flex grow flex-col gap-1">
                <span className={LABEL}>Held by</span>
                <input
                  id="token-held-by"
                  className={`${FIELD} font-mono text-[12px]`}
                  value={heldBy}
                  placeholder="laptop-claude-code"
                  onChange={(e) => setHeldBy(e.target.value)}
                  required
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className={LABEL}>Profile</span>
                <select
                  id="token-profile"
                  className={`${FIELD} font-mono text-[12px]`}
                  value={profile}
                  onChange={(e) => setProfile(e.target.value)}
                >
                  {Object.keys(tokens?.profiles ?? { reader: [] }).map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className={LABEL}>Expires</span>
                <select
                  id="token-expiry"
                  className={`${FIELD} font-mono text-[12px]`}
                  value={days === null ? 'never' : String(days)}
                  onChange={(e) => setDays(e.target.value === 'never' ? null : Number(e.target.value))}
                >
                  {EXPIRY.map((x) => (
                    <option key={x.label} value={x.days === null ? 'never' : String(x.days)}>
                      {x.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="flex items-center justify-between gap-3">
              <span className="min-w-0 flex-1 text-[11.5px] text-text-muted">
                reader reads every view · analyst adds read-only SQL
              </span>
              <button type="submit" className={BUTTON_PRIMARY} disabled={!heldBy.trim()}>
                Issue token
              </button>
            </div>
          </form>
        </Card>
      </div>

      {message ? (
        <p role="status" className="text-[12.5px] text-text-muted">
          {message}
        </p>
      ) : null}

      {issued ? (
        <div className="flex flex-col gap-3 border border-accent-graph bg-surface px-[22px] py-[18px]">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <span className="text-[14px] font-semibold text-text">
              Token {issued.row.token_id} for {issued.row.held_by}
            </span>
            <span className="font-mono text-[10.5px] text-text-muted">
              {issued.row.profile} · expires {when(issued.row.expires_at)}
            </span>
          </div>
          <div className="flex items-center gap-2.5">
            <code
              data-testid="secret"
              className="min-w-0 grow overflow-x-auto border border-line bg-ground px-3 py-2 font-mono text-[13px] text-text"
            >
              {issued.secret}
            </code>
            <button type="button" className={BUTTON_SECONDARY} onClick={() => void copy(issued.secret)}>
              Copy
            </button>
          </div>
          <p className="text-[12.5px] text-accent-attention">
            This is the only time this token is shown. Store it in the client now.{' '}
            {exposed
              ? 'This address is public: anyone holding the token can read the whole corpus from anywhere until it expires or is revoked.'
              : 'Anyone holding it who can reach /mcp can read the whole corpus until it expires or is revoked.'}
          </p>
          <div role="tablist" className="flex gap-0.5 border-b border-line">
            {CLIENTS.map((c) => (
              <button
                key={c.key}
                type="button"
                role="tab"
                aria-selected={client === c.key}
                onClick={() => setClient(c.key)}
                className={`px-3 py-1.5 text-[12px] ${
                  client === c.key ? 'border-b-2 border-accent-graph text-text' : 'text-text-muted'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
          <pre className="overflow-x-auto border border-line bg-ground px-3 py-2.5 font-mono text-[11.5px] leading-relaxed text-text">
            {setupFor(client, url, issued.secret)}
          </pre>
        </div>
      ) : null}

      <Card className="px-[22px] py-[18px]">
        <div className="flex flex-col gap-2.5">
          <div className="flex items-baseline justify-between">
            <span className={LABEL}>Tokens · {active} active</span>
            <button
              type="button"
              className="font-mono text-[11px] text-accent-graph"
              onClick={() => setShowRevoked((s) => !s)}
            >
              {showRevoked ? 'Hide revoked' : 'Show revoked'}
            </button>
          </div>
          {tokens === null ? (
            <Loading what="the tokens" />
          ) : tokens.rows.length === 0 ? (
            <p className="text-[12.5px] text-text-muted">No tokens yet. Issue one above for each device.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left">
                <thead>
                  <tr className="border-b border-line">
                    {['ID', 'Held by', 'Profile', 'Expires', 'State', ''].map((h) => (
                      <th key={h} className={`${LABEL} pb-2 pr-3`}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {tokens.rows.map((row) => (
                    <tr
                      key={row.token_id}
                      className={`border-b border-line ${row.state === 'active' ? '' : 'opacity-55'}`}
                    >
                      <td className="py-2.5 pr-3 font-mono text-[11.5px] text-text tabular-nums">{row.token_id}</td>
                      <td className="pr-3 text-[12.5px] text-text">{row.held_by}</td>
                      <td className="pr-3 font-mono text-[11.5px] text-text">{row.profile}</td>
                      <td className="pr-3 font-mono text-[11.5px] text-text">{when(row.expires_at)}</td>
                      <td className="pr-3 text-[12.5px] text-text">
                        {row.state}
                        {row.no_expiry && row.state === 'active' ? (
                          <span className="ml-2 border border-accent-attention px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.12em] text-accent-attention">
                            no expiry
                          </span>
                        ) : null}
                      </td>
                      <td className="text-right">
                        {row.state === 'active' ? (
                          <button
                            type="button"
                            className={BUTTON_SECONDARY}
                            onClick={() => void onRevoke(row.token_id)}
                          >
                            Revoke
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
    </section>
  )
}

function Reach({ label, value, muted = false }: { label: string; value: string; muted?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[12.5px] font-medium text-text">{label}</span>
      <span className={`font-mono text-[11px] ${muted ? 'text-text-faint' : 'text-text'}`}>{value}</span>
    </div>
  )
}
