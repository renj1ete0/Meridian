import { useState } from 'react'

import { ApiError } from '../lib/api'
import { useMe } from '../lib/me'
import { claimInstance } from '../lib/people'

/**
 * For someone Cloudflare Access let in who holds no permission (task `B-257`, ADR 0024): says so,
 * and, while nobody can manage access, offers to claim the instance with the code the API wrote
 * to its log. See docs/features/web-app.md#permissions.
 */
export function NoAccess({ claim = claimInstance }: { claim?: typeof claimInstance }) {
  const { me, reload } = useMe()
  const [code, setCode] = useState('')
  const [message, setMessage] = useState<string | null>(null)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setMessage(null)
    try {
      await claim(code.trim())
      await reload()
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : 'The code could not be checked.')
    }
  }

  return (
    <div className="mx-auto flex max-w-[620px] flex-col gap-4 px-6 py-16">
      <h1 className="text-[25px] font-semibold leading-[1.15] text-text">
        {me?.setup_needed ? 'Nobody manages access yet' : 'No access yet'}
      </h1>
      {me?.setup_needed ? (
        <>
          <p className="text-[13px] leading-[1.55] text-text-muted">
            This instance has no owner and nobody who can grant access. Enter the one-time code from the server&apos;s
            log (the API logs it as <span className="font-mono">setup_code</span>) to manage access yourself. Setting{' '}
            <span className="font-mono">MERIDIAN_OWNER_EMAILS</span> on the server does the same for good.
          </p>
          <form className="flex flex-wrap items-end gap-2.5" onSubmit={submit}>
            <label className="flex flex-col gap-1">
              <span className="font-mono text-[9px] font-medium uppercase tracking-[var(--tracking-label)] text-text-faint">
                Setup code
              </span>
              <input
                id="setup-code"
                className="h-[34px] border border-line-strong bg-surface px-[11px] font-mono text-[13px] uppercase text-text"
                value={code}
                onChange={(e) => setCode(e.target.value)}
                autoComplete="off"
                required
              />
            </label>
            <button
              type="submit"
              disabled={!code.trim()}
              className="inline-flex h-[34px] items-center border border-accent-graph bg-accent-graph px-[13px] text-[12.5px] font-medium text-surface disabled:opacity-45"
            >
              Claim
            </button>
          </form>
        </>
      ) : (
        <p className="text-[13px] leading-[1.55] text-text-muted">
          You are signed in{me?.subject ? ` as ${me.subject}` : ''}, but your access does not include reading this
          corpus. Ask whoever manages access here to add you.
        </p>
      )}
      {message ? (
        <p role="alert" className="text-[12.5px] text-accent-attention">
          {message}
        </p>
      ) : null}
    </div>
  )
}
