/**
 * Admin → Assistant access (task B-146, ADRs 0003 and 0011): the secret is shown once, the
 * address is the one the browser used, exposure is warned about, and revoking works.
 */
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AssistantAccessPanel } from '../src/admin/AssistantAccessPanel'
import { SECTIONS } from '../src/admin/sections'
import { ApiError } from '../src/lib/api'
import { isPublic, mcpUrlFrom, setupFor, type TokenRow, type Tokens } from '../src/lib/tokens'

afterEach(cleanup)

const row = (over: Partial<TokenRow> = {}): TokenRow => ({
  token_id: 1,
  held_by: 'laptop',
  profile: 'reader',
  tools: ['search_chunks'],
  created_at: '2026-10-01T02:00:00Z',
  expires_at: '2026-12-30T02:00:00Z',
  state: 'active',
  no_expiry: false,
  ...over,
})

const tokens = (rows: TokenRow[], public_url: string | null = null): Tokens => ({
  rows,
  profiles: { reader: ['search_chunks'], analyst: ['search_chunks', 'run_readonly_query'] },
  public_url,
})

describe('helpers', () => {
  it('builds the MCP address from the origin, with or without a trailing slash', () => {
    expect(mcpUrlFrom('https://example.org')).toBe('https://example.org/mcp')
    expect(mcpUrlFrom('https://example.org/')).toBe('https://example.org/mcp')
  })

  it.each([
    ['https://example.org/mcp', true],
    ['http://example.org/mcp', false],
    ['https://localhost/mcp', false],
    ['https://192.168.1.4/mcp', false],
    ['https://10.0.0.2/mcp', false],
    ['https://100.70.1.1/mcp', false],
    ['https://box.local/mcp', false],
    ['not a url', false],
  ])('isPublic(%s) is %s', (url, expected) => {
    expect(isPublic(url)).toBe(expected)
  })

  it('puts the secret and URL in every client setup', () => {
    for (const client of ['claude-code', 'gemini-cli', 'other'] as const) {
      const text = setupFor(client, 'https://x.example/mcp', 'mrd_secret')
      expect(text).toContain('https://x.example/mcp')
      expect(text).toContain('Bearer mrd_secret')
    }
  })

  it('lists the section under system', () => {
    expect(SECTIONS.find((s) => s.key === 'access')).toMatchObject({ path: 'access', group: 'system' })
  })
})

describe('AssistantAccessPanel', () => {
  it('shows the address this browser reached and says when no public URL is set', async () => {
    render(<AssistantAccessPanel origin="http://192.168.1.4:8080" load={async () => tokens([])} />)
    expect(await screen.findByText('http://192.168.1.4:8080/mcp')).toBeTruthy()
    expect(screen.getByText(/no public URL is configured/)).toBeTruthy()
    expect(screen.getByText(/No tokens yet/)).toBeTruthy()
  })

  it('issues a token, shows the secret once, and forgets it on revoke', async () => {
    let rows = [row()]
    const issued = row({ token_id: 2, held_by: 'phone', no_expiry: true, expires_at: null })
    const issue = vi.fn(async () => {
      rows = [issued, ...rows]
      return { row: issued, secret: 'mrd_onetime' }
    })
    const revoke = vi.fn(async (id: number) => {
      rows = rows.map((r) => (r.token_id === id ? { ...r, state: 'revoked' } : r))
      return rows.find((r) => r.token_id === id)!
    })
    render(
      <AssistantAccessPanel
        origin="https://site.example"
        load={async () => tokens(rows)}
        issue={issue}
        revoke={revoke}
      />,
    )
    await screen.findByText('laptop')
    fireEvent.change(screen.getByPlaceholderText('laptop-claude-code'), { target: { value: '  phone ' } })
    fireEvent.change(screen.getByLabelText('Expires'), { target: { value: 'never' } })
    fireEvent.click(screen.getByRole('button', { name: 'Issue token' }))

    expect(await screen.findByTestId('secret')).toHaveProperty('textContent', 'mrd_onetime')
    expect(issue).toHaveBeenCalledWith({ held_by: 'phone', profile: 'reader', days: null })
    // A public address gets the stronger warning; the setup carries the secret.
    expect(screen.getByText(/This address is public/)).toBeTruthy()
    expect(screen.getByText(/claude mcp add/).textContent).toContain('Bearer mrd_onetime')
    expect(screen.getByText('no expiry')).toBeTruthy()

    fireEvent.click(screen.getByRole('tab', { name: 'Gemini CLI' }))
    expect(screen.getByText(/"httpUrl"/)).toBeTruthy()

    const revokes = screen.getAllByRole('button', { name: 'Revoke' })
    fireEvent.click(revokes[0]!)
    await waitFor(() => expect(screen.queryByTestId('secret')).toBeNull())
    expect(revoke).toHaveBeenCalledWith(2)
  })

  it('warns less loudly on a private address', async () => {
    const issue = async () => ({ row: row({ token_id: 3 }), secret: 'mrd_x' })
    render(<AssistantAccessPanel origin="http://localhost:8080" load={async () => tokens([])} issue={issue} />)
    fireEvent.change(await screen.findByPlaceholderText('laptop-claude-code'), { target: { value: 'desk' } })
    fireEvent.click(screen.getByRole('button', { name: 'Issue token' }))
    await screen.findByTestId('secret')
    expect(screen.queryByText(/This address is public/)).toBeNull()
    expect(screen.getByText(/who can reach \/mcp/)).toBeTruthy()
  })

  it('will not issue with an empty holder', async () => {
    const issue = vi.fn()
    render(<AssistantAccessPanel origin="http://localhost" load={async () => tokens([])} issue={issue} />)
    fireEvent.change(await screen.findByPlaceholderText('laptop-claude-code'), { target: { value: '   ' } })
    expect(screen.getByRole('button', { name: 'Issue token' })).toHaveProperty('disabled', true)
  })

  it("shows the server's refusal and no secret", async () => {
    const issue = async () => {
      throw new ApiError(422, 'held_by: string does not match pattern')
    }
    render(<AssistantAccessPanel origin="http://localhost" load={async () => tokens([])} issue={issue} />)
    fireEvent.change(await screen.findByPlaceholderText('laptop-claude-code'), { target: { value: 'bad/name' } })
    fireEvent.click(screen.getByRole('button', { name: 'Issue token' }))
    expect(await screen.findByRole('status')).toHaveProperty('textContent', expect.stringContaining('does not match'))
    expect(screen.queryByTestId('secret')).toBeNull()
  })

  it('asks for revoked rows only when shown, and fades them', async () => {
    const load = vi.fn(async (all: boolean) =>
      tokens(all ? [row(), row({ token_id: 9, held_by: 'old', state: 'revoked' })] : [row()]),
    )
    render(<AssistantAccessPanel origin="http://localhost" load={load} />)
    await screen.findByText('laptop')
    expect(load).toHaveBeenLastCalledWith(false)
    fireEvent.click(screen.getByRole('button', { name: 'Show revoked' }))
    const old = await screen.findByText('old')
    expect(load).toHaveBeenLastCalledWith(true)
    expect(old.closest('tr')?.className).toContain('opacity-55')
    // Only the active row can be revoked.
    expect(screen.getAllByRole('button', { name: 'Revoke' })).toHaveLength(1)
    expect(screen.getByText(/1 active/)).toBeTruthy()
  })

  it('reports a failed load instead of spinning forever', async () => {
    const load = async () => {
      throw new ApiError(503, 'Admin is not configured')
    }
    render(<AssistantAccessPanel origin="http://localhost" load={load} />)
    expect(await screen.findByRole('status')).toHaveProperty('textContent', 'Admin is not configured')
  })
})
