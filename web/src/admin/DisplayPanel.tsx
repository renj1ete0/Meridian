import { useEffect, useState } from 'react'

import { BUTTON_PRIMARY, Card, LABEL, PageHeader } from './ui'
import { ApiError, displaySettings, setDisplayTimezone, type DisplaySettings } from '../lib/api'
import { clockOf, setDisplayZone, zoneLabel } from '../lib/time'

/** Every IANA zone the browser knows, or a short list where it cannot say. */
export function knownZones(): string[] {
  const supported = (Intl as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf
  return supported ? supported('timeZone') : ['Asia/Singapore', 'UTC', 'Europe/London']
}

export interface DisplayPanelProps {
  /** Injected in tests; the page passes nothing and the panel loads and saves itself. */
  initial?: DisplaySettings
  save?: (zone: string) => Promise<DisplaySettings>
}

/**
 * The zone every time is shown in (`B-145`, ADR 0009). Stored times are UTC and never move;
 * this changes only how they read, everywhere: pages, notifications, the digest.
 */
export function DisplayPanel({ initial, save = setDisplayTimezone }: DisplayPanelProps) {
  const [settings, setSettings] = useState<DisplaySettings | null>(initial ?? null)
  const [choice, setChoice] = useState(initial?.display_timezone ?? '')
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    if (initial) return
    const controller = new AbortController()
    displaySettings({ signal: controller.signal })
      .then((loaded) => {
        setSettings(loaded)
        setChoice(loaded.display_timezone)
      })
      .catch(() => setMessage('Could not read the current setting.'))
    return () => controller.abort()
  }, [initial])

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    try {
      const saved = await save(choice)
      setDisplayZone(saved.display_timezone)
      setSettings(saved)
      setMessage(`Saved. Times now read in ${saved.display_timezone} (${saved.label}).`)
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : 'Could not save the setting.')
    }
  }

  const now = new Date()
  return (
    <section className="flex flex-col gap-5">
      <PageHeader title="Display">
        The time zone every time is shown in. Times are stored in UTC and do not change; only how
        they read does. Budget months and the daily question cap still reset at UTC midnight.
      </PageHeader>
      <Card>
        <form className="flex flex-wrap items-end gap-4" onSubmit={submit}>
          <label className="flex flex-col gap-1.5">
            <span className={LABEL}>Time zone</span>
            <select
              aria-label="Time zone"
              className="h-[26px] border border-line-strong bg-surface px-2 font-mono text-[11.5px] text-text"
              value={choice}
              onChange={(e) => setChoice(e.target.value)}
            >
              {knownZones().map((zone) => (
                <option key={zone} value={zone}>
                  {zone} · {zoneLabel(now, zone)}
                </option>
              ))}
            </select>
          </label>
          <button
            type="submit"
            className={BUTTON_PRIMARY}
            disabled={!choice || choice === settings?.display_timezone}
          >
            Save
          </button>
          {settings ? (
            <span className="font-mono text-[12px] text-text-muted">
              Now {clockOf(now, settings.display_timezone)} {settings.label}
            </span>
          ) : null}
        </form>
        {message ? <p className="mt-3 text-[12.5px] text-text-muted">{message}</p> : null}
      </Card>
    </section>
  )
}
