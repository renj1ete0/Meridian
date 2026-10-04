import { useState } from 'react'

import type { AgentRow } from '../lib/api'
import { BUTTON_ROW, PageHeader, ROW, TD, TDM, TH, TableCard } from './ui'

/**
 * The agent registry (task P6-23, spec §11.3, §11.11, §11.12).
 *
 * §11.3's argument for a registry is that swapping models should be a config
 * row rather than a code change. This screen is where somebody reads that row
 * and turns one on — which is the last step before the graph has an edge, and
 * the first step that spends money.
 *
 * **The failure this screen exists to end is between the rows, not in one.**
 * Routing picks an agent by task type, so a type no enabled and usable row
 * declares is a stage that defers every run — and every individual row looks
 * perfectly fine, because nothing is wrong with any of them. `unserved` is
 * therefore the first thing on the screen rather than a detail under the
 * table.
 *
 * **A row says why it cannot be used, in routing's words.** An enabled agent
 * whose key variable is unset looks exactly like a working one until a run
 * defers hours later. The server computes those reasons, because a client that
 * derived its own would eventually disagree with the router that actually
 * refuses — and it would disagree in the bad direction, showing a green light
 * for an agent nothing can reach.
 *
 * **No key, anywhere.** §11.11 keeps credentials out of the database because it
 * is snapshotted off-device. The row names the variable; the screen reports
 * only whether it is set where the API runs.
 *
 * **The model is the one string worth changing here.** A local server names its
 * models however it was started, so the row that points at it has to follow —
 * and that is an edit, not a release. A `${VAR}` value is read from the
 * environment where the call is made, as the endpoint is.
 */

export interface AgentsPanelProps {
  rows: readonly AgentRow[]
  unserved: readonly string[]
  busy?: string | null
  onToggle?: (agentId: string, enabled: boolean) => void
  /** Save a new model string; resolves `true` once the server took it. */
  onModel?: (agentId: string, model: string) => Promise<boolean>
}

/** The model as typed, or why it cannot be saved: the server's rule, early. */
export function modelProblem(value: string): string | null {
  if (value.trim() === '') return 'A model string is required.'
  if (value !== value.trim()) return 'No spaces at either end.'
  if (value.length > 200) return 'At most 200 characters.'
  return null
}

function ModelField({
  row,
  busy,
  onModel,
}: {
  row: AgentRow
  busy: boolean
  onModel?: (agentId: string, model: string) => Promise<boolean>
}) {
  const [draft, setDraft] = useState<string | null>(null)
  // The exact string, because `P4-15` found the seeded registry shipping a
  // placeholder — and a placeholder is only recognisable if it is shown as
  // written.
  const shown = row.model ?? 'no model string'
  if (draft === null) {
    return (
      <div className="flex items-baseline gap-2 text-[11px] text-text-faint">
        <span data-model>{shown}</span>
        {onModel ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => setDraft(row.model ?? '')}
            className="font-mono text-[11px] text-accent-graph hover:underline disabled:opacity-45"
            aria-label={`Change the model for ${row.agent_id}`}
          >
            change
          </button>
        ) : null}
      </div>
    )
  }
  const problem = modelProblem(draft)
  return (
    <form
      className="mt-1 flex flex-col gap-1"
      onSubmit={(event) => {
        event.preventDefault()
        if (problem || !onModel) return
        void onModel(row.agent_id, draft).then((saved) => {
          if (saved) setDraft(null)
        })
      }}
    >
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        aria-label={`Model for ${row.agent_id}`}
        className="h-[26px] w-full border border-line-strong bg-surface px-2 font-mono text-[11.5px] text-text"
        autoFocus
      />
      {problem ? <span className="text-[11px] text-accent-attention">{problem}</span> : null}
      <span className="flex gap-1.5">
        <button type="submit" disabled={busy || problem !== null} className={BUTTON_ROW}>
          Save
        </button>
        <button type="button" onClick={() => setDraft(null)} className={BUTTON_ROW}>
          Cancel
        </button>
      </span>
    </form>
  )
}

/** The one-line summary of a row's state, in the order that matters.
 *
 * Blocked reasons win over "ready": an agent that is enabled and unreachable is
 * the case this screen exists for, and leading with "enabled" would bury it.
 */
export function rowState(row: AgentRow): { label: string; tone: 'ready' | 'blocked' | 'off' } {
  if (row.blocked_by.length > 0) {
    return { label: row.blocked_by.join('; '), tone: row.enabled ? 'blocked' : 'off' }
  }
  return { label: 'ready', tone: 'ready' }
}

/** Quality tier as §11.12 means it: ordinal, and not a score out of anything. */
export function tierLabel(tier: number | null): string {
  return tier === null ? 'no tier' : `tier ${tier}`
}

export function AgentsPanel({ rows, unserved, busy, onToggle, onModel }: AgentsPanelProps) {
  return (
    <section className="flex flex-col gap-5">
      <PageHeader title="Agent registry">
        Which model serves each stage. Routing picks an agent by task type, so what matters is whether every type has
        one it can actually reach.
      </PageHeader>

      {unserved.length > 0 ? (
        <p className="border border-accent-attention bg-surface px-[18px] py-3.5 text-[12.5px] text-accent-attention">
          Nothing serves {unserved.join(', ')}. A run reaching {unserved.length > 1 ? 'those' : 'that'} stage will defer
          rather than fail, and the registry below will look fine while it does.
        </p>
      ) : (
        <p className="text-[12.5px] text-text-muted">Every task type has an agent that can serve it.</p>
      )}

      {rows.length === 0 ? (
        <p className="border border-line bg-surface px-[18px] py-4 text-[12.5px] text-text-muted">
          No agents are registered. They are seeded from <code>config/agents.yaml</code> at first boot.
        </p>
      ) : (
        <TableCard>
          <thead>
            <tr>
              <th className={TH}>Agent</th>
              <th className={TH}>Serves</th>
              <th className={TH}>Provider</th>
              <th className={TH}>Key</th>
              <th className={TH}>Routing</th>
              <th className={TH}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const state = rowState(row)
              return (
                <tr key={row.agent_id} className={ROW} data-agent={row.agent_id}>
                  <td className={`${TDM} min-w-[180px]`}>
                    <div className="text-text">{row.agent_id}</div>
                    <ModelField row={row} busy={busy === row.agent_id} onModel={onModel} />
                  </td>
                  <td className={`${TDM} text-text-muted`}>
                    {row.task_types?.length ? row.task_types.join(', ') : 'declares nothing'}
                  </td>
                  <td className={`${TDM} whitespace-nowrap text-text-muted`}>
                    {row.provider} · {tierLabel(row.quality_tier)}
                    {row.cost_tier ? ` · ${row.cost_tier}` : ''}
                    {row.route_order != null ? ` · tried ${row.route_order}` : ''}
                  </td>
                  <td className={`${TD} text-[12px] text-text-muted`}>
                    {row.api_key_env_var ? (
                      <>
                        <code className="text-text">{row.api_key_env_var}</code>
                        {row.key_present ? ', which is set here.' : ', which is not set here.'}
                      </>
                    ) : (
                      'None needed.'
                    )}
                  </td>
                  <td
                    className={`${TD} text-[12px] ${
                      state.tone === 'blocked'
                        ? 'text-accent-attention'
                        : state.tone === 'ready'
                          ? 'text-text'
                          : 'text-text-muted'
                    }`}
                  >
                    {state.label}
                  </td>
                  <td className={`${TD} text-right`}>
                    <button
                      type="button"
                      disabled={busy === row.agent_id}
                      onClick={() => onToggle?.(row.agent_id, !row.enabled)}
                      className={BUTTON_ROW}
                    >
                      {row.enabled ? 'Disable' : 'Enable'}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </TableCard>
      )}
    </section>
  )
}
