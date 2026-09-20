/**
 * The annotation surface (task P6-05, spec §12.5, §12.6).
 *
 * §12.5 says the thing this file is mostly testing: "build the affordance early
 * or it won't get used". So the assertions are about reachability and about
 * what the note carries, not about markup — a composer that renders beautifully
 * and drops the citation is the failure this layer cannot survive.
 *
 * Three properties are load-bearing, and each has a test that fails loudly if
 * the component quietly stops holding it:
 *
 * 1. **A note with no target is writable.** The thought that has not found its
 *    node yet is the one the corpus cannot re-derive, and phase 4 has not run,
 *    so on this deployment it is *every* note. A composer that required a
 *    target would make the feature unavailable exactly when §12.5 wants it.
 * 2. **The citation survives the round trip.** The reader ticks passages, and
 *    what leaves the component has to be those passages.
 * 3. **Nothing here claims authorship.** `produced_by` is assigned by the
 *    server; a draft carrying one would be refused, and a component that sent
 *    one would be asking to be trusted about the one thing it cannot be.
 */
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  NoteComposer,
  NoteList,
  NotesPanel,
  describeAttachment,
} from '../src/explore/Annotations'
import type { Annotation, AnnotationTarget } from '../src/lib/api'

afterEach(cleanup)

function target(over: Partial<AnnotationTarget> = {}): AnnotationTarget {
  return { entity_id: 7, canonical_name: 'Silver Zone', node_type: 'scheme', ...over }
}

function note(over: Partial<Annotation> = {}): Annotation {
  return {
    entity_id: 101,
    title: 'The pilot count does not match the press release',
    body: 'Four precincts here, seven in the announcement.',
    about: [target()],
    supporting_chunk_ids: [12, 13],
    topic_labels: null,
    produced_by: 'human',
    produced_at: '2026-09-18T09:00:00Z',
    created_at: '2026-03-01T09:00:00Z',
    ...over,
  }
}

function open() {
  fireEvent.click(screen.getByRole('button', { name: /write a note/i }))
}

function write(title: string) {
  fireEvent.change(screen.getByLabelText(/title for this note/i), { target: { value: title } })
  fireEvent.click(screen.getByRole('button', { name: /keep it/i }))
}

describe('the composer is reachable', () => {
  it('is offered with nothing selected and nothing to attach to', () => {
    // §12.5's whole argument. On this deployment the graph is empty, so a
    // composer gated on having a target would never appear at all.
    render(<NoteComposer />)

    expect(screen.getByRole('button', { name: /write a note/i })).toBeTruthy()
  })

  it('writes a note that is about nothing', () => {
    const onWrite = vi.fn()
    render(<NoteComposer onWrite={onWrite} />)

    open()
    write('A thought with no home yet')

    expect(onWrite).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'A thought with no home yet', about: [] }),
    )
  })

  it('can be backed out of', () => {
    render(<NoteComposer />)
    open()

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

    expect(screen.getByRole('button', { name: /write a note/i })).toBeTruthy()
  })

  it('refuses a note with no title rather than inventing one', () => {
    // A note titled "Untitled" is one the reader cannot find again, which for
    // this layer is the same as not having written it.
    const onWrite = vi.fn()
    render(<NoteComposer onWrite={onWrite} />)
    open()
    fireEvent.change(screen.getByLabelText(/title for this note/i), { target: { value: '   ' } })

    fireEvent.click(screen.getByRole('button', { name: /keep it/i }))

    expect(onWrite).not.toHaveBeenCalled()
  })
})

describe('what the note carries', () => {
  it('sends the passages the reader ticked', () => {
    const onWrite = vi.fn()
    render(<NoteComposer citing={[12, 13]} onWrite={onWrite} />)

    open()
    write('These two disagree')

    expect(onWrite).toHaveBeenCalledWith(
      expect.objectContaining({ supporting_chunk_ids: [12, 13] }),
    )
  })

  it('sends the ids of what it is about, from the targets it was given', () => {
    const onWrite = vi.fn()
    render(<NoteComposer about={[target({ entity_id: 7 }), target({ entity_id: 9 })]} onWrite={onWrite} />)

    open()
    write('Same programme, two names')

    expect(onWrite).toHaveBeenCalledWith(expect.objectContaining({ about: [7, 9] }))
  })

  it('never claims authorship', () => {
    // `AnnotationCreate` forbids extra keys, so a draft carrying `produced_by`
    // would be refused outright — and the layer is only distinguishable while
    // the server is the only thing that can set it.
    const onWrite = vi.fn()
    render(<NoteComposer about={[target()]} citing={[12]} onWrite={onWrite} />)

    open()
    write('Mine')

    const draft = onWrite.mock.calls[0]![0] as Record<string, unknown>
    expect(Object.keys(draft)).not.toContain('produced_by')
    expect(Object.keys(draft)).not.toContain('model')
    expect(Object.keys(draft)).not.toContain('quality_tier')
  })

  it('records an empty body as absent rather than as empty text', () => {
    // Otherwise "a title with no note" and "a note whose text was cleared"
    // become the same row, and the export cannot tell them apart either.
    const onWrite = vi.fn()
    render(<NoteComposer onWrite={onWrite} />)

    open()
    write('Title only')

    expect(onWrite).toHaveBeenCalledWith(expect.objectContaining({ body: null }))
  })
})

describe('the composer says what it will carry before it is written', () => {
  it('names the node and counts the passages', () => {
    expect(describeAttachment([target()], [12, 13])).toBe('About Silver Zone, citing 2 passages.')
  })

  it('says so when a note is attached to nothing', () => {
    // Stated rather than left blank. "Attached to nothing" is a fact about the
    // note, and a reader who discovers it months later discovers it by clicking
    // a citation that goes nowhere.
    expect(describeAttachment([], [])).toBe('Attached to nothing yet, citing no passage.')
  })

  it('agrees in number for a single passage', () => {
    expect(describeAttachment([], [12])).toBe('Attached to nothing yet, citing 1 passage.')
  })

  it('shows it in the form, not only in a prop', () => {
    render(<NoteComposer about={[target()]} citing={[12]} />)
    open()

    expect(screen.getByText(/About Silver Zone, citing 1 passage\./)).toBeTruthy()
  })
})

describe('reading notes back', () => {
  it('shows the note and what it is about', () => {
    render(<NoteList notes={[note()]} />)

    expect(screen.getByText(/pilot count does not match/i)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Silver Zone' })).toBeTruthy()
  })

  it('dates a note by when it was written, not when the row appeared', () => {
    // A note rewritten this morning is a note the reader touched this morning.
    // Dating it by `created_at` files it under the month it was first typed.
    render(<NoteList notes={[note()]} />)

    expect(screen.getByText(/written 2026-09-18/)).toBeTruthy()
    expect(screen.queryByText(/written 2026-03-01/)).toBeNull()
  })

  it('falls back to when the row appeared for a note never rewritten', () => {
    render(<NoteList notes={[note({ produced_at: null })]} />)

    expect(screen.getByText(/written 2026-03-01/)).toBeTruthy()
  })

  it('does not repeat the node a reader is already looking at', () => {
    render(<NoteList notes={[note()]} inContextOf={7} />)

    expect(screen.queryByRole('link', { name: 'Silver Zone' })).toBeNull()
  })

  it('still links the other nodes a note spans', () => {
    // The interesting case §12.5 names — "these two disagree" — and the chip
    // that matters is the one pointing somewhere the reader is not.
    render(
      <NoteList
        notes={[note({ about: [target(), target({ entity_id: 9, canonical_name: 'Green Link' })] })]}
        inContextOf={7}
      />,
    )

    expect(screen.getByRole('link', { name: 'Green Link' })).toBeTruthy()
  })

  it('says a note cites nothing by saying nothing about passages', () => {
    const { container } = render(<NoteList notes={[note({ supporting_chunk_ids: [] })]} />)

    expect(container.textContent).not.toMatch(/passage/)
  })

  it('names the layer rather than rendering an empty list', () => {
    render(<NoteList notes={[]} />)

    expect(screen.getByText(/cannot be re-derived/i)).toBeTruthy()
  })
})

describe('the notes panel on the landing screen', () => {
  it('reports how many exist, not how many came back', () => {
    // A reader with four hundred notes is owed the number; a panel that only
    // ever reports its own length cannot tell them.
    render(<NotesPanel notes={[note()]} total={400} />)

    expect(screen.getByText(/400 notes, 1 shown\./)).toBeTruthy()
  })

  it('does not say "shown" when the page is everything there is', () => {
    render(<NotesPanel notes={[note()]} total={1} />)

    expect(screen.getByText(/^1 note\.$/)).toBeTruthy()
  })

  it('offers the Markdown export once there is something to export', () => {
    // §12.5: "avoid trapping material in a bespoke store". A plain link, so it
    // can be copied, opened in a tab or piped through curl.
    render(<NotesPanel notes={[note()]} total={1} />)

    const link = screen.getByRole('link', { name: /export as markdown/i })
    expect(link.getAttribute('href')).toBe('/api/explore/export/annotations')
  })

  it('offers no export when there is nothing written', () => {
    render(<NotesPanel notes={[]} total={0} />)

    expect(screen.queryByRole('link', { name: /export as markdown/i })).toBeNull()
  })

  it('says what the layer is rather than showing an empty panel', () => {
    render(<NotesPanel notes={[]} total={0} />)

    expect(screen.getByText(/cannot be recovered by crawling again/i)).toBeTruthy()
  })
})

describe('a refusal', () => {
  it('shows the API’s own sentence', () => {
    // A rejected citation names the chunk that did not resolve; a write on an
    // instance without Access names the variable that would allow it. Both are
    // thrown away by a generic "could not save".
    render(<NoteComposer error="No such chunk: [99]." />)
    open()

    expect(screen.getByText(/No such chunk: \[99\]\./)).toBeTruthy()
  })

  it('keeps the reader from firing a second write while one is in flight', () => {
    render(<NoteComposer busy onWrite={vi.fn()} />)
    open()
    fireEvent.change(screen.getByLabelText(/title for this note/i), { target: { value: 'x' } })

    expect(screen.getByRole('button', { name: /keep it/i }).hasAttribute('disabled')).toBe(true)
  })
})
