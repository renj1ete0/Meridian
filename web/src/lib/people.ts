/**
 * People and permissions (task `B-252`, ADR 0024). Mirrors `meridian_core/schemas/people.py`;
 * `tests/people.test.ts` holds the field lists to it.
 */
import { request } from './api'

type Equal<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false
type Expect<T extends true> = T

/** Mirrors `PermissionRead`. */
export interface PermissionInfo {
  name: string
  label: string
  describes: string
  group: string
}
export const PERMISSION_FIELDS = ['name', 'label', 'describes', 'group'] as const
export type AssertPermission = Expect<Equal<keyof PermissionInfo, (typeof PERMISSION_FIELDS)[number]>>

/** Mirrors `MeRead`: who the app is talking to, and what to show them. */
export interface Me {
  subject: string | null
  profile: string | null
  owner: boolean
  permissions: string[]
  setup_needed: boolean
}
export const ME_FIELDS = ['subject', 'profile', 'owner', 'permissions', 'setup_needed'] as const
export type AssertMe = Expect<Equal<keyof Me, (typeof ME_FIELDS)[number]>>

/** Mirrors `PersonRead`. */
export interface Person {
  grant_id: number | null
  subject: string
  profile: string
  owner: boolean
  allow: string[]
  deny: string[]
  permissions: string[]
  expires_at: string | null
  state: 'active' | 'expired' | 'revoked'
  note: string | null
}
export const PERSON_FIELDS = [
  'grant_id',
  'subject',
  'profile',
  'owner',
  'allow',
  'deny',
  'permissions',
  'expires_at',
  'state',
  'note',
] as const
export type AssertPerson = Expect<Equal<keyof Person, (typeof PERSON_FIELDS)[number]>>

/** Mirrors `PeopleRead`. */
export interface People {
  people: Person[]
  catalogue: PermissionInfo[]
  profiles: Record<string, string[]>
}
export const PEOPLE_FIELDS = ['people', 'catalogue', 'profiles'] as const
export type AssertPeople = Expect<Equal<keyof People, (typeof PEOPLE_FIELDS)[number]>>

/** What one permission is set to for one person, against their profile. */
export type Setting = 'profile' | 'on' | 'off'

/** A grant's switches read back as one setting per permission. */
export function settingOf(person: Pick<Person, 'allow' | 'deny'>, permission: string): Setting {
  if (person.deny.includes(permission)) return 'off'
  if (person.allow.includes(permission)) return 'on'
  return 'profile'
}

/**
 * The switches that express `settings`: a permission set "on" goes in `allow`, "off" in
 * `deny`, "profile" in neither. In catalogue order, so a save that changes nothing sends
 * what it read.
 */
export function switchesFor(
  settings: Record<string, Setting>,
  catalogue: readonly PermissionInfo[],
): { allow: string[]; deny: string[] } {
  const allow: string[] = []
  const deny: string[] = []
  for (const { name } of catalogue) {
    if (settings[name] === 'on') allow.push(name)
    if (settings[name] === 'off') deny.push(name)
  }
  return { allow, deny }
}

/** What a profile plus switches adds up to, as the server computes it (`effective`). */
export function holds(
  profile: readonly string[],
  settings: Record<string, Setting>,
  catalogue: readonly PermissionInfo[],
): Set<string> {
  const base = new Set(profile)
  return new Set(
    catalogue
      .map((p) => p.name)
      .filter((name) => settings[name] !== 'off' && (settings[name] === 'on' || base.has(name))),
  )
}

export function getMe(init?: RequestInit): Promise<Me> {
  return request<Me>('/api/me', init)
}

export function claimInstance(code: string, init?: RequestInit): Promise<Me> {
  return request<Me>('/api/setup/claim', {
    ...init,
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ code }),
  })
}

export function getPeople(revoked = false, init?: RequestInit): Promise<People> {
  return request<People>(`/api/admin/people${revoked ? '?revoked=true' : ''}`, init)
}

export function addPerson(
  body: { subject: string; profile: string; days: number; allow?: string[]; deny?: string[] },
  init?: RequestInit,
): Promise<Person> {
  return request<Person>('/api/admin/people', {
    ...init,
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function editPerson(
  grantId: number,
  change: { profile?: string; allow?: string[]; deny?: string[]; days?: number; revoked?: boolean },
  init?: RequestInit,
): Promise<Person> {
  return request<Person>(`/api/admin/people/${grantId}`, {
    ...init,
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(change),
  })
}
