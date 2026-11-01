/**
 * Admin → People & permissions (task B-257, ADR 0024): a permission set Off or On becomes the
 * right switch, what a person would hold follows the profile and switches as the server
 * computes it, owners are listed and never edited, and Admin shows a person only the sections
 * their permissions open.
 */
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PeoplePanel } from '../src/admin/PeoplePanel'
import { SECTIONS, sectionNeeds } from '../src/admin/sections'
import { MeProvider } from '../src/lib/me'
import { BY_NAME_LABEL } from '../src/lib/permissionLabels'
import { holds, settingOf, switchesFor, type People, type Person, type PermissionInfo } from '../src/lib/people'
import { NoAccess } from '../src/ui/NoAccess'

afterEach(cleanup)

const CATALOGUE: PermissionInfo[] = [
  { name: 'explore.read', label: 'Read the corpus', describes: 'Find', group: 'Reading' },
  { name: 'admin.view', label: 'Open Admin', describes: 'Status', group: 'Reading' },
  { name: 'chat.ask', label: 'Ask', describes: 'A model answers', group: 'Writing their own work' },
  { name: 'seeds.add', label: 'Seeds', describes: 'URLs', group: 'Steering the crawl' },
  { name: 'access.manage', label: 'People & permissions', describes: 'This page', group: 'The system' },
]
const PROFILES = {
  reader: ['explore.read'],
  analyst: ['explore.read', 'admin.view', 'chat.ask'],
  operator: ['explore.read', 'admin.view', 'chat.ask', 'seeds.add'],
}

const person = (over: Partial<Person> = {}): Person => ({
  grant_id: 7,
  subject: 'colleague@example.test',
  profile: 'analyst',
  owner: false,
  allow: [],
  deny: [],
  permissions: PROFILES.analyst,
  expires_at: '2027-11-01T00:00:00Z',
  state: 'active',
  note: null,
  ...over,
})

const people = (rows: Person[]): People => ({ people: rows, catalogue: CATALOGUE, profiles: PROFILES })

const OWNER: Person = person({
  grant_id: null,
  subject: 'owner@example.test',
  profile: 'owner',
  owner: true,
  expires_at: null,
})

describe('switches', () => {
  it('reads a grant back as one setting per permission', () => {
    const p = person({ allow: ['seeds.add'], deny: ['chat.ask'] })
    expect(settingOf(p, 'seeds.add')).toBe('on')
    expect(settingOf(p, 'chat.ask')).toBe('off')
    expect(settingOf(p, 'admin.view')).toBe('profile')
  })

  it('writes settings as allow and deny in catalogue order, leaving Profile out of both', () => {
    expect(
      switchesFor({ 'access.manage': 'on', 'seeds.add': 'on', 'chat.ask': 'off', 'admin.view': 'profile' }, CATALOGUE),
    ).toEqual({ allow: ['seeds.add', 'access.manage'], deny: ['chat.ask'] })
  })

  it('adds up as the server does: profile, plus On, minus Off', () => {
    const held = holds(PROFILES.analyst, { 'seeds.add': 'on', 'chat.ask': 'off' }, CATALOGUE)
    expect([...held].sort()).toEqual(['admin.view', 'explore.read', 'seeds.add'])
  })
})

describe('PeoplePanel', () => {
  function renderPanel(rows: Person[]) {
    const load = vi.fn(async () => people(rows))
    const edit = vi.fn(async (_id: number, change: Record<string, unknown>) => person(change as Partial<Person>))
    const add = vi.fn(async () => person({ grant_id: 9, subject: 'new@example.test' }))
    render(<PeoplePanel load={load} edit={edit} add={add} />)
    return { load, edit, add }
  }

  it('turns a permission Off into a deny switch and saves only what changed', async () => {
    const { edit } = renderPanel([OWNER, person()])
    fireEvent.click(await screen.findByRole('button', { name: 'colleague@example.test' }))
    const save = screen.getByRole('button', { name: 'Save' })
    expect((save as HTMLButtonElement).disabled).toBe(true)

    const ask = screen.getByRole('radiogroup', { name: 'Ask' })
    fireEvent.click(within(ask).getByRole('radio', { name: 'Off' }))
    fireEvent.click(save)

    await waitFor(() => expect(edit).toHaveBeenCalledTimes(1))
    expect(edit).toHaveBeenCalledWith(7, { profile: 'analyst', allow: [], deny: ['chat.ask'] })
  })

  it('shows what a profile change would hold before it is saved', async () => {
    renderPanel([person({ profile: 'reader', permissions: PROFILES.reader })])
    fireEvent.click(await screen.findByRole('button', { name: 'colleague@example.test' }))
    const row = (label: string) => screen.getByRole('radiogroup', { name: label }).parentElement as HTMLElement
    expect(within(row('Seeds')).getByLabelText('does not hold')).toBeTruthy()
    fireEvent.change(screen.getByRole('combobox', { name: 'Profile for colleague@example.test' }), {
      target: { value: 'operator' },
    })
    expect(within(row('Seeds')).getByLabelText('holds')).toBeTruthy()
  })

  it('lists an owner from the environment and offers nothing to change', async () => {
    renderPanel([OWNER])
    const cell = await screen.findByText('owner@example.test')
    const tr = cell.closest('tr') as HTMLElement
    expect(within(tr).getByText('from environment')).toBeTruthy()
    expect(within(tr).queryByRole('button')).toBeNull()
  })

  it('revokes from the row', async () => {
    const { edit } = renderPanel([person()])
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }))
    await waitFor(() => expect(edit).toHaveBeenCalledWith(7, { revoked: true }))
  })

  it('shows the server’s refusal as written', async () => {
    const load = vi.fn(async () => people([person()]))
    const { ApiError } = await import('../src/lib/api')
    const edit = vi.fn(async () => {
      throw new ApiError(422, 'this change would take away your own access to this page')
    })
    render(<PeoplePanel load={load} edit={edit} add={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Revoke' }))
    expect(await screen.findByText(/take away your own access/)).toBeTruthy()
  })
})

describe('sections', () => {
  it('gates every section on a permission the catalogue knows', () => {
    for (const def of SECTIONS) expect(BY_NAME_LABEL[sectionNeeds(def)], def.key).toBeDefined()
    const edits = SECTIONS.flatMap((def) => (def.edits ? [def.edits] : []))
    expect(edits.filter((name) => BY_NAME_LABEL[name] === undefined)).toEqual([])
  })

  it('keeps people and tokens to those who manage access', () => {
    const managed = SECTIONS.filter((s) => sectionNeeds(s) === 'access.manage').map((s) => s.key)
    expect(managed.sort()).toEqual(['access', 'people'])
  })
})

describe('NoAccess', () => {
  it('offers the claim form only while nobody can manage access, and reloads after a claim', async () => {
    let calls = 0
    const load = vi.fn(async () => {
      calls += 1
      return {
        subject: 'first@example.test',
        profile: calls > 1 ? 'operator' : null,
        owner: false,
        permissions: calls > 1 ? ['explore.read', 'access.manage'] : [],
        setup_needed: calls === 1,
      }
    })
    const claim = vi.fn(async () => ({}) as never)
    render(
      <MeProvider load={load}>
        <NoAccess claim={claim} />
      </MeProvider>,
    )
    fireEvent.change(await screen.findByLabelText('Setup code'), { target: { value: ' ab12cd34 ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Claim' }))
    await waitFor(() => expect(claim).toHaveBeenCalledWith('ab12cd34'))
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
  })

  it('says who is signed in when there is nothing to claim', async () => {
    const load = vi.fn(async () => ({
      subject: 'stranger@example.test',
      profile: null,
      owner: false,
      permissions: [],
      setup_needed: false,
    }))
    render(
      <MeProvider load={load}>
        <NoAccess />
      </MeProvider>,
    )
    expect(await screen.findByText(/signed in as stranger@example.test/)).toBeTruthy()
    expect(screen.queryByLabelText('Setup code')).toBeNull()
  })
})
