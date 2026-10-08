import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

import {
  DEFAULT_ZONE,
  clockOf,
  dayOf,
  daysBetween,
  displayZone,
  setDisplayZone,
  shortDayOf,
  stampOf,
  startOfDayIso,
  zoneLabel,
} from '../src/lib/time'

// 2026-10-04T18:30:00Z is 02:30 on the 5th in Singapore, and still the 4th in New York.
const LATE = '2026-10-04T18:30:00Z'

afterEach(() => setDisplayZone(DEFAULT_ZONE))

describe('the display zone (B-145, ADR 0009)', () => {
  it('defaults to Singapore', () => {
    expect(displayZone()).toBe('Asia/Singapore')
  })

  it('shows an instant on the right calendar day in the zone, not in UTC', () => {
    expect(dayOf(LATE)).toBe('2026-10-05')
    expect(clockOf(LATE)).toBe('02:30')
    expect(stampOf(LATE)).toBe('2026-10-05 02:30')
    expect(shortDayOf(LATE)).toBe('05 Oct')
    expect(dayOf(LATE, 'America/New_York')).toBe('2026-10-04')
  })

  it('labels offsets as a reader says them, including half hours and summer time', () => {
    expect(zoneLabel(LATE, 'Asia/Singapore')).toBe('GMT+8')
    expect(zoneLabel(LATE, 'Asia/Kathmandu')).toBe('GMT+5:45')
    expect(zoneLabel(LATE, 'UTC')).toBe('GMT')
    expect(zoneLabel('2026-07-01T12:00:00Z', 'America/New_York')).toBe('GMT-4')
    expect(zoneLabel('2026-01-15T12:00:00Z', 'America/New_York')).toBe('GMT-5')
  })

  it('labels an instant the way Intl does, whatever its seconds (B-166)', () => {
    // Every earlier case was a whole minute; a real "as of" never is.
    const zones = [
      'Asia/Singapore',
      'Asia/Kathmandu',
      'UTC',
      'America/New_York',
      'America/St_Johns',
      'Australia/Adelaide',
    ]
    const intl = (at: Date, inZone: string) =>
      new Intl.DateTimeFormat('en-US', { timeZone: inZone, timeZoneName: 'shortOffset' })
        .formatToParts(at)
        .find((part) => part.type === 'timeZoneName')!
        .value.replace(/^GMT\+0$/, 'GMT') // ours says plain GMT
    for (const inZone of zones) {
      for (let second = 0; second < 60; second += 1) {
        const at = new Date(Date.UTC(2026, 9, 8, 12, 34, second, (second * 37) % 1000))
        expect(zoneLabel(at, inZone), `${inZone} at :${second}`).toBe(intl(at, inZone))
      }
    }
    expect(zoneLabel('2026-10-08T12:34:45.900Z', 'Asia/Singapore')).toBe('GMT+8')
  })

  it('finds the start of a day in the zone from an instant with seconds too', () => {
    expect(dayOf('2026-10-04T15:59:59.999Z')).toBe('2026-10-04')
    expect(dayOf('2026-10-04T16:00:00.000Z')).toBe('2026-10-05')
  })

  it('puts the start of a picked day at midnight in the zone', () => {
    expect(startOfDayIso('2026-10-05', 'Asia/Singapore')).toBe('2026-10-04T16:00:00Z')
    expect(startOfDayIso('2026-10-05', 'UTC')).toBe('2026-10-05T00:00:00Z')
    expect(startOfDayIso('2026-07-01', 'America/New_York')).toBe('2026-07-01T04:00:00Z')
  })

  it('counts calendar days, not 24-hour spans', () => {
    expect(daysBetween('2026-10-04', '2026-10-05')).toBe(1)
    expect(daysBetween('2026-02-28', '2026-03-01')).toBe(1)
    expect(daysBetween('2026-10-05', '2026-10-05')).toBe(0)
  })

  it('switches every format when the zone changes, and ignores a name it does not know', () => {
    setDisplayZone('UTC')
    expect(stampOf(LATE)).toBe('2026-10-04 18:30')
    setDisplayZone('Mars/Olympus_Mons')
    expect(displayZone()).toBe('UTC')
  })
})

describe('times are formatted only in lib/time.ts', () => {
  // Formatting with getHours() uses the browser's zone; slicing an ISO string shows UTC.
  // Either one puts a second clock on the page (ADR 0009).
  const FORBIDDEN = [
    /\.getHours\(\)/,
    /\.getUTC(Date|Month|Hours)\(\)/,
    /\.slice\(0, ?1[0-6]\)\.replace\('T'/,
    /_at\??\.slice\(0, ?10\)/,
    /toLocale(Date|Time)String\(/,
  ]

  function files(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const path = join(dir, name)
      return statSync(path).isDirectory() ? files(path) : /\.tsx?$/.test(name) ? [path] : []
    })
  }

  it('finds no other formatter in the app', () => {
    const offenders = files(join(__dirname, '../src'))
      .filter((path) => !path.endsWith('lib/time.ts'))
      .flatMap((path) =>
        readFileSync(path, 'utf8')
          .split('\n')
          .map((line, i) => [line, i + 1] as const)
          .filter(([line]) => FORBIDDEN.some((re) => re.test(line)))
          .map(([, n]) => `${path.split('/src/')[1]}:${n}`),
      )
    expect(offenders).toEqual([])
  })
})
