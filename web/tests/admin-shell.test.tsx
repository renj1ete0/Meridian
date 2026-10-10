// @vitest-environment jsdom
/**
 * Admin's shell (`B-199`): one landmark, every section reachable on a phone, and a staged
 * weight change kept, and marked, while the operator looks elsewhere.
 */
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AddedTopic, AdminPage } from '../src/admin/AdminPage'
import { SECTIONS } from '../src/admin/sections'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  window.history.pushState({}, '', '/')
})

async function open(path: string) {
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>(() => {})),
  )
  window.history.pushState({}, '', path)
  await act(async () => {
    render(<AdminPage />)
  })
}

describe('the shell', () => {
  it('adds no second main landmark inside the app’s', async () => {
    await open('/admin/runs')
    expect(document.querySelectorAll('main')).toHaveLength(0)
  })

  it('offers every section in one picker for narrow screens, the current one chosen', async () => {
    await open('/admin/runs')
    const picker = screen.getByRole('combobox', { name: 'Admin section' }) as HTMLSelectElement
    expect(
      Array.from(picker.options)
        .map((o) => o.value)
        .sort(),
    ).toEqual(SECTIONS.map((s) => s.key).sort())
    expect(picker.value).toBe('runs')
  })
})

describe('after adding a topic (B-196)', () => {
  it('says what it needs next and links there', () => {
    const onDismiss = vi.fn()
    render(<AddedTopic topic="kerbside" onDismiss={onDismiss} />)
    expect(screen.getByText('Added “kerbside”.')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Seed it →' }).getAttribute('href')).toBe('/admin/seeds')
    expect(screen.getByRole('link', { name: 'Watch the first fetches →' }).getAttribute('href')).toBe('/admin/crawl')
    screen.getByRole('button', { name: 'dismiss' }).click()
    expect(onDismiss).toHaveBeenCalled()
  })
})
