/**
 * A topic's description (task P2-21).
 *
 * Content labelling compares every page to a topic's name, description and
 * vocabulary, so the description is the operator's main lever on which pages a
 * topic claims. The tests pin the three things that are easy to get wrong: an
 * empty edit clears rather than saves whitespace, an unchanged edit sends
 * nothing (every save re-labels the corpus), and the missing state says what
 * it costs rather than being a blank line.
 */
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DescriptionLine, TopicPanel } from '../src/admin/TopicPanel'
import { TOPIC_CONFIG_FIELDS, type TopicConfig, type TopicRow } from '../src/lib/api'

afterEach(cleanup)

function row(description: string | null): TopicRow {
  const topic: TopicConfig = {
    topic: 'walkability',
    weight: 0.5,
    floor: 0.05,
    ceiling: 0.8,
    boost_factor: null,
    boost_expires_at: null,
    pinned: false,
    status: 'active',
    description,
  }
  return { topic, effective_weight: 0.5, share: 0.5, boost_active: false }
}

describe('DescriptionLine', () => {
  it('says what a missing description costs', () => {
    render(<DescriptionLine topic="walkability" description={null} onDescribe={() => {}} />)
    expect(screen.getByText(/matched on the name and vocabulary alone/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Describe walkability' }).textContent).toBe('Describe')
  })

  it('saves the trimmed text', () => {
    const onDescribe = vi.fn()
    render(<DescriptionLine topic="walkability" description={null} onDescribe={onDescribe} />)
    fireEvent.click(screen.getByRole('button', { name: 'Describe walkability' }))
    fireEvent.change(screen.getByLabelText('Description for walkability'), {
      target: { value: '  how easy a place is to walk  ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onDescribe).toHaveBeenCalledWith('walkability', 'how easy a place is to walk')
  })

  it('clears with null, never with whitespace', () => {
    const onDescribe = vi.fn()
    render(<DescriptionLine topic="walkability" description="old" onDescribe={onDescribe} />)
    fireEvent.click(screen.getByRole('button', { name: 'Describe walkability' }))
    fireEvent.change(screen.getByLabelText('Description for walkability'), {
      target: { value: '   ' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(onDescribe).toHaveBeenCalledWith('walkability', null)
  })

  it('refuses to save an unchanged description, since every save re-labels the corpus', () => {
    const onDescribe = vi.fn()
    render(<DescriptionLine topic="walkability" description="same" onDescribe={onDescribe} />)
    fireEvent.click(screen.getByRole('button', { name: 'Describe walkability' }))
    fireEvent.change(screen.getByLabelText('Description for walkability'), {
      target: { value: ' same ' },
    })
    const save = screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(true)
    fireEvent.click(save)
    expect(onDescribe).not.toHaveBeenCalled()
  })

  it('cancelling restores the stored text and sends nothing', () => {
    const onDescribe = vi.fn()
    render(<DescriptionLine topic="walkability" description="kept" onDescribe={onDescribe} />)
    fireEvent.click(screen.getByRole('button', { name: 'Describe walkability' }))
    fireEvent.change(screen.getByLabelText('Description for walkability'), {
      target: { value: 'discarded' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onDescribe).not.toHaveBeenCalled()
    expect(screen.getByText('kept')).toBeTruthy()
  })

  it('offers no editor when the page cannot write', () => {
    render(<DescriptionLine topic="walkability" description="read only" />)
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('TopicPanel', () => {
  it('shows each topic’s description from the row it was given', () => {
    render(<TopicPanel rows={[row('a sentence about it')]} sumsTo={1} onDescribe={() => {}} />)
    expect(screen.getByText('a sentence about it')).toBeTruthy()
  })

  it('the description is a field the API mirror carries', () => {
    // Drift guard: the panel reads `topic.description`, so the field list the
    // API mirror is checked against must carry it.
    expect(TOPIC_CONFIG_FIELDS).toContain('description')
  })
})
