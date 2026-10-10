import { useEffect, useState } from 'react'

import { BUTTON_PRIMARY, Card, LABEL, PageHeader } from './ui'
import { ApiError, displaySettings, setDisplayTimezone, type DisplaySettings } from '../lib/api'
import { clockOf, setDisplayZone, zoneLabel } from '../lib/time'

/** Every IANA zone the browser knows, or a short list where it cannot say. */
export function knownZones(): string[] {
  const supported = (Intl as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf
  return supported ? supported('timeZone') : ['Asia/Singapore', 'UTC', 'Europe/London']
}

/**
 * The zones a typed text could mean (`B-210`). Over four hundred zones sit in one list, and a
 * select's type-ahead matches only from the start, so "singapore" never reaches
 * `Asia/Singapore`. Any part of the name matches, a space stands for the underscore the names
 * use, and an offset such as `GMT+8` matches every zone at it now. The zone already chosen is
 * always kept, so narrowing never silently changes the choice, and kept even when the browser's
 * list lacks it: `supportedValuesOf` omits `UTC`, which a select would then show as its first
 * option.
 */
export function zonesMatching(zones: readonly string[], text: string, chosen: string, now: Date): string[] {
  const needle = text.trim().toLowerCase().replace(/\s+/g, '_')
  if (!needle) return chosen && !zones.includes(chosen) ? [chosen, ...zones] : [...zones]
  const offset = needle.toUpperCase()
  const found = zones.filter(
    (zone) => zone.toLowerCase().includes(needle) || zoneLabel(now, zone).toUpperCase() === offset,
  )
  return chosen && !found.includes(chosen) ? [chosen, ...found] : found
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
  const [narrow, setNarrow] = useState('')

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
  const zones = zonesMatching(knownZones(), narrow, choice, now)
  function narrowTo(text: string) {
    setNarrow(text)
    // One match is the zone meant; picking it saves a second step.
    const found = zonesMatching(knownZones(), text, '', now)
    if (found.length === 1) setChoice(found[0]!)
  }
  return (
    <section className="flex flex-col gap-5">
      <PageHeader title="Display">
        The time zone every time is shown in. Times are stored in UTC and do not change; only how they read does. Budget
        months and the daily question cap still reset at UTC midnight.
      </PageHeader>
      <Card>
        <form className="flex flex-wrap items-end gap-4" onSubmit={submit}>
          <label className="flex flex-col gap-1.5">
            <span className={LABEL}>Find a zone</span>
            <input
              type="search"
              aria-label="Find a zone"
              placeholder="City or GMT+8"
              className="h-[26px] w-44 border border-line-strong bg-surface px-2 font-mono text-[11.5px] text-text placeholder:text-text-faint"
              value={narrow}
              onChange={(e) => narrowTo(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className={LABEL}>Time zone</span>
            <select
              aria-label="Time zone"
              className="h-[26px] border border-line-strong bg-surface px-2 font-mono text-[11.5px] text-text"
              value={choice}
              onChange={(e) => setChoice(e.target.value)}
            >
              {zones.length === 0 ? <option value="">No zone matches</option> : null}
              {zones.map((zone) => (
                <option key={zone} value={zone}>
                  {zone} · {zoneLabel(now, zone)}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className={BUTTON_PRIMARY} disabled={!choice || choice === settings?.display_timezone}>
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
