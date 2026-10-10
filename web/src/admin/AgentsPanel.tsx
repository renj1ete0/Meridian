import { useState } from 'react'

import type { AgentRow } from '../lib/api'
import { BUTTON_ROW, PageHeader, ROW, TD, TDM, TH, TableCard } from './ui'

/**
 * The agent registry (task P6-23, spec §11.3, §11.11, §11.12): unserved task types first,
 * each row's unusable reason in routing's words (computed by the server), no key ever
 * shown, and the model editable. See docs/features/web-app.md#agent-registry.
 */

export interface AgentsPanelProps {
  rows: readonly AgentRow[]
  unserved: readonly string[]
  busy?: string | null
  onToggle?: (agentId: string, enabled: boolean) => void
  /** Save a new model string; resolves `true` once the server took it. */
  onModel?: (agentId: string, model: string) => Promise<boolean>
}

/**
 * What a model string means, beside it as written (`B-199`): `${HOSTED_LLM_MODEL}` is a
 * variable the server reads at call time, and `<fill in …>` is a placeholder nobody replaced.
 * The string itself stays on screen, since `P4-15` found a placeholder recognisable only as
 * written. Null when the string is a model name and needs no note.
 */
export function modelNote(model: string | null): string | null {
  if (model === null || !model.trim()) return null
  const variable = /^\$\{([A-Z0-9_]+)\}$/.exec(model.trim())
  if (variable) return `read from ${variable[1]} in the environment`
  if (model.trim().startsWith('<')) return 'a placeholder: set a model before enabling'
  return null
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
  const note = modelNote(row.model)
  if (draft === null) {
    return (
      <div className="flex flex-wrap items-baseline gap-x-2 text-[11px] text-text-faint">
        <span data-model>{shown}</span>
        {note ? <span className="font-sans text-text-muted">{note}</span> : null}
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
          Nothing serves {unserved.join(', ')}. A run reaching {unserved.length > 1 ? 'those stages' : 'that stage'}{' '}
          will defer rather than fail, and the registry below will look fine while it does.
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
