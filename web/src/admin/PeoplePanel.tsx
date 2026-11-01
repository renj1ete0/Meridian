import { useCallback, useEffect, useMemo, useState } from 'react'

import { Badge, BUTTON_PRIMARY, BUTTON_ROW, Card, FIELD, LABEL, Loading, PageHeader } from './ui'
import { ApiError } from '../lib/api'
import { useMe } from '../lib/me'
import {
  addPerson,
  editPerson,
  getPeople,
  holds,
  settingOf,
  switchesFor,
  type People,
  type Person,
  type Setting,
} from '../lib/people'
import { stampOf } from '../lib/time'

/**
 * Admin → People & permissions (task `B-257`, ADR 0024; mock `docs/design/AdminPeople.dc.html`).
 *
 * Add a person by their Access email with a profile, switch single permissions on or off for
 * them, and revoke. Owners come from the server's environment and are listed, never edited.
 * See docs/features/web-app.md#people-and-permissions.
 */

const EXPIRY: readonly { days: number; label: string }[] = [
  { days: 30, label: '30 days' },
  { days: 90, label: '90 days' },
  { days: 365, label: '1 year' },
  { days: 730, label: '2 years' },
]

const SETTINGS: readonly { key: Setting; label: string }[] = [
  { key: 'profile', label: 'Profile' },
  { key: 'on', label: 'On' },
  { key: 'off', label: 'Off' },
]

const day = (iso: string | null) => (iso ? stampOf(iso).slice(0, 10) : '—')

export interface PeoplePanelProps {
  /** Injected in tests; the page loads and acts through the API itself. */
  load?: typeof getPeople
  add?: typeof addPerson
  edit?: typeof editPerson
}

export function PeoplePanel({ load = getPeople, add = addPerson, edit = editPerson }: PeoplePanelProps) {
  const { me, reload: reloadMe } = useMe()
  const [data, setData] = useState<People | null>(null)
  const [showRevoked, setShowRevoked] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const [subject, setSubject] = useState('')
  const [profile, setProfile] = useState('reader')
  const [days, setDays] = useState(365)
  const [message, setMessage] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setData(await load(showRevoked))
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : 'The people could not be read.')
    }
  }, [load, showRevoked])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const person = data?.people.find((p) => p.grant_id !== null && p.grant_id === selected) ?? null

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setMessage(null)
    try {
      const made = await add({ subject: subject.trim(), profile, days })
      setSubject('')
      await refresh()
      setSelected(made.grant_id)
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : 'The person could not be added.')
    }
  }

  async function change(grantId: number, body: Parameters<typeof editPerson>[1], done: string) {
    setMessage(null)
    try {
      const saved = await edit(grantId, body)
      await refresh()
      if (saved.subject === me?.subject) await reloadMe()
      setMessage(done)
    } catch (cause) {
      setMessage(cause instanceof ApiError ? cause.message : 'The change could not be saved.')
    }
  }

  const live = data?.people.filter((p) => p.state === 'active').length ?? 0

  return (
    <section className="flex flex-col gap-5">
      <PageHeader title="People & permissions">
        Who can read the corpus and who can change what. Cloudflare Access decides who reaches this site; here each
        person gets a profile, and switches that add or remove single permissions from it. Owners are set in the
        server&apos;s environment and always hold everything.
      </PageHeader>

      {message ? (
        <p role="status" className="text-[12.5px] text-text-muted">
          {message}
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_500px]">
        <div className="flex min-w-0 flex-col gap-4">
          <Card className="px-5 py-4">
            <form className="flex flex-col gap-2.5" onSubmit={submit}>
              <span className={LABEL}>Add a person</span>
              <div className="flex flex-wrap items-end gap-2.5">
                <label className="flex min-w-[220px] grow flex-col gap-1">
                  <span className={LABEL}>Email, as Cloudflare Access reports it</span>
                  <input
                    id="person-subject"
                    type="email"
                    className={`${FIELD} font-mono text-[12px]`}
                    value={subject}
                    placeholder="colleague@example.org"
                    onChange={(e) => setSubject(e.target.value)}
                    required
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className={LABEL}>Profile</span>
                  <select
                    id="person-profile"
                    className={`${FIELD} font-mono text-[12px]`}
                    value={profile}
                    onChange={(e) => setProfile(e.target.value)}
                  >
                    {Object.keys(data?.profiles ?? { reader: [] }).map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className={LABEL}>Expires</span>
                  <select
                    id="person-expiry"
                    className={`${FIELD} font-mono text-[12px]`}
                    value={String(days)}
                    onChange={(e) => setDays(Number(e.target.value))}
                  >
                    {EXPIRY.map((x) => (
                      <option key={x.days} value={String(x.days)}>
                        {x.label}
                      </option>
                    ))}
                  </select>
                </label>
                <button type="submit" className={BUTTON_PRIMARY} disabled={!subject.trim()}>
                  Add
                </button>
              </div>
              <span className="text-[11.5px] text-text-muted">
                reader: the reader pages · analyst adds notes, saved views, Ask and a read-only Admin · operator holds
                everything but people &amp; permissions
              </span>
            </form>
          </Card>

          <Card className="px-5 py-4">
            <div className="flex flex-col gap-2">
              <div className="flex items-baseline justify-between">
                <span className={LABEL}>People · {live}</span>
                <button
                  type="button"
                  className="font-mono text-[11px] text-accent-graph"
                  onClick={() => setShowRevoked((s) => !s)}
                >
                  {showRevoked ? 'Hide revoked' : 'Show revoked'}
                </button>
              </div>
              {data === null ? (
                <Loading what="the people" />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-left">
                    <thead>
                      <tr className="border-b border-line">
                        {['Person', 'Profile', 'Switches', 'Expires', 'State', ''].map((h) => (
                          <th key={h} className={`${LABEL} pb-2 pr-3`}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {data.people.map((row) => (
                        <PersonRow
                          key={row.grant_id ?? `owner-${row.subject}`}
                          person={row}
                          labels={labelsOf(data)}
                          selected={row.grant_id !== null && row.grant_id === selected}
                          onSelect={() => setSelected(row.grant_id)}
                          onRevoke={() =>
                            row.grant_id !== null &&
                            void change(row.grant_id, { revoked: true }, `${row.subject} no longer has access.`)
                          }
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </Card>
        </div>

        {data !== null && person !== null ? (
          <PermissionsCard
            key={`${person.grant_id}-${person.allow.join()}-${person.deny.join()}-${person.profile}`}
            person={person}
            data={data}
            onSave={(body) => change(person.grant_id as number, body, `Saved ${person.subject}.`)}
          />
        ) : (
          <Card className="px-5 py-4">
            <p className="text-[12.5px] text-text-muted">
              Choose a person to see and change what they may do. Owners are not edited here.
            </p>
          </Card>
        )}
      </div>
    </section>
  )
}

function labelsOf(data: People): Record<string, string> {
  return Object.fromEntries(data.catalogue.map((p) => [p.name, p.label]))
}

function PersonRow({
  person,
  labels,
  selected,
  onSelect,
  onRevoke,
}: {
  person: Person
  labels: Record<string, string>
  selected: boolean
  onSelect: () => void
  onRevoke: () => void
}) {
  const short = (name: string) => (labels[name] ?? name).toLowerCase()
  return (
    <tr
      className={`border-b border-line ${selected ? 'bg-surface-raised' : ''} ${
        person.state === 'active' ? '' : 'opacity-55'
      }`}
    >
      <td className="py-2.5 pr-3 text-[12.5px] text-text">
        {person.owner ? (
          person.subject
        ) : (
          <button type="button" className="text-left hover:underline" aria-pressed={selected} onClick={onSelect}>
            <span className={selected ? 'font-medium' : ''}>{person.subject}</span>
          </button>
        )}
      </td>
      <td className="pr-3 font-mono text-[11.5px] text-text">{person.profile}</td>
      <td className="pr-3">
        {person.owner ? (
          <Badge>from environment</Badge>
        ) : person.allow.length + person.deny.length === 0 ? (
          <span className="text-[12.5px] text-text-faint">none</span>
        ) : (
          <span className="flex flex-wrap gap-1">
            {person.allow.map((p) => (
              <span
                key={`+${p}`}
                className="border border-accent-graph px-1.5 py-[2px] font-mono text-[9px] uppercase leading-none tracking-[0.12em] text-accent-graph"
              >
                + {short(p)}
              </span>
            ))}
            {person.deny.map((p) => (
              <Badge key={`-${p}`} tone="attention">
                − {short(p)}
              </Badge>
            ))}
          </span>
        )}
      </td>
      <td className="pr-3 font-mono text-[11.5px] text-text tabular-nums">{day(person.expires_at)}</td>
      <td className="pr-3 text-[12.5px] text-text">{person.state}</td>
      <td className="text-right">
        {!person.owner && person.state === 'active' ? (
          <button type="button" className={BUTTON_ROW} onClick={onRevoke}>
            Revoke
          </button>
        ) : null}
      </td>
    </tr>
  )
}

function PermissionsCard({
  person,
  data,
  onSave,
}: {
  person: Person
  data: People
  onSave: (body: { profile: string; allow: string[]; deny: string[] }) => Promise<void>
}) {
  const [profile, setProfile] = useState(person.profile)
  const [settings, setSettings] = useState<Record<string, Setting>>(() =>
    Object.fromEntries(data.catalogue.map((p) => [p.name, settingOf(person, p.name)])),
  )
  const held = useMemo(() => holds(data.profiles[profile] ?? [], settings, data.catalogue), [data, profile, settings])
  const groups = useMemo(() => [...new Set(data.catalogue.map((p) => p.group))], [data])
  const switches = switchesFor(settings, data.catalogue)
  const changed =
    profile !== person.profile ||
    switches.allow.join() !== person.allow.join() ||
    switches.deny.join() !== person.deny.join()

  return (
    <Card className="flex flex-col gap-2.5 px-5 py-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-[14px] font-semibold text-text">{person.subject}</span>
        <span className="font-mono text-[10.5px] text-text-muted">
          {switches.allow.length + switches.deny.length} switches · expires {day(person.expires_at)}
        </span>
      </div>
      <label className="flex items-center gap-2">
        <span className={LABEL}>Profile</span>
        <select
          aria-label={`Profile for ${person.subject}`}
          className={`${FIELD} h-[30px] font-mono text-[12px]`}
          value={profile}
          onChange={(e) => setProfile(e.target.value)}
        >
          {Object.keys(data.profiles).map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </label>
      <p className="text-[11.5px] text-text-muted">
        Each permission follows the profile unless switched on or off for this person. ✓ is what they would hold.
      </p>
      <div className="flex flex-col">
        {groups.map((group) => (
          <div key={group} className="flex flex-col">
            <div className={`${LABEL} pb-1 pt-3`}>{group}</div>
            {data.catalogue
              .filter((p) => p.group === group)
              .map((p) => (
                <div
                  key={p.name}
                  className="flex flex-wrap items-center gap-x-3 gap-y-0.5 border-b border-line/60 py-1.5"
                >
                  <span
                    aria-label={held.has(p.name) ? 'holds' : 'does not hold'}
                    className={`w-3.5 text-center text-[12px] ${held.has(p.name) ? 'text-accent-graph' : 'text-text-faint'}`}
                  >
                    {held.has(p.name) ? '✓' : '·'}
                  </span>
                  <span className="min-w-0 flex-1 text-[12.5px] text-text sm:w-[170px] sm:flex-none">{p.label}</span>
                  {/* Below sm, under the label: beside it the column was a word wide. */}
                  <span className="order-last w-full pl-[26px] text-[11.5px] text-text-muted sm:order-none sm:w-auto sm:min-w-0 sm:flex-1 sm:pl-0">
                    {p.describes}
                  </span>
                  <div role="radiogroup" aria-label={p.label} className="flex shrink-0 border border-line-strong">
                    {SETTINGS.map((s) => (
                      <button
                        key={s.key}
                        type="button"
                        role="radio"
                        aria-checked={settings[p.name] === s.key}
                        onClick={() => setSettings((all) => ({ ...all, [p.name]: s.key }))}
                        className={`border-l border-line px-2.5 py-[3px] text-[11px] first:border-l-0 ${
                          settings[p.name] === s.key ? 'bg-accent-graph text-surface' : 'text-text-muted'
                        }`}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
          </div>
        ))}
      </div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <span className="text-[11.5px] text-text-muted">Changes are recorded with who made them.</span>
        <button
          type="button"
          className={BUTTON_PRIMARY}
          disabled={!changed}
          onClick={() => void onSave({ profile, ...switches })}
        >
          Save
        </button>
      </div>
    </Card>
  )
}
