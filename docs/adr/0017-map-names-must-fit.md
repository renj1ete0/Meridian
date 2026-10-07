# 0017. A Map name must fit what it names

- **Status:** Accepted
- **Date:** 2026-10-07
- **Tasks:** `B-157` (amends `B-74`)

## Context

`B-74` named Map areas from a fixed list of research fields and subfields, by the label nearest
each area's centroid, so that page furniture could never become a name. On a live build the
top level read "Speech and Hearing", "Algebra and Number Theory", "Geometry and Topology" and
"Emergency Medical Services", over regions whose own terms were statutes, occupational
statistics, mixed science and congressional documents.

The list had no names for most of what a public-sector corpus holds: legislation,
appropriations, regulations, forms, official statistics, site pages. The match accepted nearly
any fit. And a region took the subfield with the most passages among its areas, which for a
mixed region could be a seventh of it.

## Decision

- Add a "Public Records" group to `config/fields.yaml`: kinds of document rather than fields of
  research, kept beside OpenAlex's list and marked as not from it.
- A subfield names an area only above a similarity floor measured on the live build (0.20 after
  centring); below it the area keeps its terms.
- A region is named from its areas, never its own centroid: by what holds a majority of its
  passages, a subfield or else a field; else by its two largest, if together they hold a fair
  share; else by its terms.

## Consequences

- Fewer wrong subjects on the Map, and more plain term names (on the live build three of
  twelve regions, and about one area in seven below them).
- The floor and shares are numbers for this embedding model; another model needs them measured
  again.
- A term name can be plain ("Data"); that was judged better than a name that asserts the wrong
  subject.
