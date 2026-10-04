# 0009. Times are stored in UTC and shown in one display zone

- **Status:** Accepted
- **Date:** 2026-10-04
- **Tasks:** `B-145`

## Context

Every timestamp column is `timestamptz`, and the API serialises times as ISO 8601 with an
offset. Display is inconsistent, though: some screens format in the browser's own time zone,
others in UTC, so one page can show two clocks. The operator works in GMT+8 and wants every
time shown there by default, with a way to change it.

## Decision

- **Storage and transport do not change.** Instants are stored as `timestamptz` (UTC) and sent
  as ISO 8601 with an explicit offset (`Z` or `+00:00`). Calendar facts that have no time of
  day, such as a publication date, stay `date`, and are never shifted between zones.
- **One display zone.** A deployment setting, `display_timezone` (an IANA name), defaults to
  `Asia/Singapore` (GMT+8) and can be changed in Admin. Every time a person reads (web pages,
  the digest, Telegram messages, reports) is converted to it. A single formatting module does
  the conversion on each side (`web/src/lib/time.ts`, `meridian_core.timefmt`).
- **Boundaries that mean money stay in UTC.** The monthly budget and the Ask panel's daily
  token cap reset at UTC boundaries, because that is where provider billing resets. Where a
  screen shows one of these boundaries, it says it is UTC.

## Consequences

- Changing the zone changes only how times read; nothing stored moves.
- A display zone is per deployment, not per person. A per-viewer override can be added later
  in browser storage without changing this record.
- Tests format with an explicit zone, so they do not depend on the machine's clock settings.

## Alternatives considered

- **The browser's own zone.** It is right for a single viewer, but the digest and Telegram
  have no browser, and the same deployment would read differently on two machines.
- **Store local times.** Rejected: a zone change or daylight-saving rule would silently corrupt
  ordering and comparisons. UTC storage is the standard practice for this reason.
