/**
 * Times as people read them: the deployment's display zone (task `B-145`, ADR 0009).
 *
 * The API sends instants as ISO 8601 with an offset; this module is the only place they are
 * turned into text. The zone comes from `/api/explore/settings` at start-up
 * (`Asia/Singapore` until it answers). Never format with `getHours()` or by slicing an ISO
 * string: the first uses the browser's zone and the second shows UTC.
 */

export const DEFAULT_ZONE = 'Asia/Singapore'

let zone = DEFAULT_ZONE

/** Set the display zone; an unknown name is ignored and the current zone kept. */
export function setDisplayZone(name: string): void {
  try {
    new Intl.DateTimeFormat('en-GB', { timeZone: name })
    zone = name
  } catch {
    // An unknown zone would throw on every format; keep the one that works.
  }
}

/** The current display zone (an IANA name). */
export function displayZone(): string {
  return zone
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

interface Parts {
  year: number
  month: number
  day: number
  hour: number
  minute: number
}

/** The wall-clock parts of an instant in the display zone. */
export function partsOf(at: Date | string, inZone: string = zone): Parts {
  const date = typeof at === 'string' ? new Date(at) : at
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: inZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(date)
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0)
  return {
    year: get('year'),
    month: get('month'),
    day: get('day'),
    hour: get('hour'),
    minute: get('minute'),
  }
}

const pad = (n: number) => String(n).padStart(2, '0')

/** `2026-10-05`: the calendar day of an instant in the display zone. */
export function dayOf(at: Date | string, inZone: string = zone): string {
  const p = partsOf(at, inZone)
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`
}

/** `08:00`: the clock time of an instant in the display zone. */
export function clockOf(at: Date | string, inZone: string = zone): string {
  const p = partsOf(at, inZone)
  return `${pad(p.hour)}:${pad(p.minute)}`
}

/** `2026-10-05 08:00`: date and time in the display zone, unlabelled. */
export function stampOf(at: Date | string, inZone: string = zone): string {
  return `${dayOf(at, inZone)} ${clockOf(at, inZone)}`
}

/** `05 Oct`: a short day in the display zone. */
export function shortDayOf(at: Date | string, inZone: string = zone): string {
  const p = partsOf(at, inZone)
  return `${pad(p.day)} ${MONTHS[p.month - 1]}`
}

/** `GMT+8`: the display zone's offset at an instant, as a reader says it. */
export function zoneLabel(at: Date | string = new Date(), inZone: string = zone): string {
  const minutes = zoneOffsetMinutes(typeof at === 'string' ? new Date(at) : at, inZone)
  if (minutes === 0) return 'GMT'
  const sign = minutes > 0 ? '+' : '-'
  const hours = Math.floor(Math.abs(minutes) / 60)
  const rest = Math.abs(minutes) % 60
  return `GMT${sign}${hours}${rest ? `:${pad(rest)}` : ''}`
}

/** Days between two calendar days (`YYYY-MM-DD`), counted in whole days. */
export function daysBetween(earlier: string, later: string): number {
  return Math.round((utcMidnight(later) - utcMidnight(earlier)) / 86_400_000)
}

/** `YYYY-MM-DD` as a UTC midnight timestamp, for arithmetic on calendar days. */
function utcMidnight(day: string): number {
  const [y = 1970, m = 1, d = 1] = day.split('-').map(Number)
  return Date.UTC(y, m - 1, d)
}

/** The UTC instant at which a calendar day starts in the display zone, as ISO 8601. */
export function startOfDayIso(day: string, inZone: string = zone): string {
  // Start from UTC midnight, then remove the zone's offset at that moment.
  const guess = new Date(utcMidnight(day))
  const offset = zoneOffsetMinutes(guess, inZone)
  return new Date(guess.getTime() - offset * 60_000).toISOString().replace('.000Z', 'Z')
}

function zoneOffsetMinutes(at: Date, inZone: string): number {
  const p = partsOf(at, inZone)
  return Math.round((Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute) - at.getTime()) / 60_000)
}
