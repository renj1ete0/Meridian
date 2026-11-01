# 0024. People get permissions per area, from a role with per-person switches

- **Status:** Accepted
- **Date:** 2026-11-01
- **Tasks:** `B-252`

## Context

Cloudflare Access decides who reaches the site; past it, the web Admin was all-or-nothing: anyone
Access let in could change topics, the budget, model routing and fetch policy. Grants
(`P3-06`, shared-read-access §3) already describe a person by their Access identity, with a
profile (`reader`, `analyst`, `operator`) and an expiry, but only MCP tokens were held to them.
The operator asked for permissions that separate viewing from changing, finer than three
profiles, editable from the web, and for a way to reach Admin on a fresh instance before anyone
has been granted anything.

## Decision

Every request through Access is resolved to a person and their permissions. A permission names
one area and one verb (`topics.steer`, `budget.edit`, `access.manage`, …) from a fixed list in
code. A person's grant carries a profile, which is a preset set of permissions, and two lists of
switches: permissions added to the profile and permissions taken away. Every admin route declares
the permission it needs, and the reader pages need `explore.read`.

Owners are named in the environment (`MERIDIAN_OWNER_EMAILS`): they hold every permission, and
nothing in the web can remove or reduce them, so no edit locks the operator out. When Access is
configured and no owner is named and nobody holds `access.manage`, the API writes a one-time
code to its log; the first person to enter it is granted `operator` with `access.manage`.

A person Access lets in who has no live grant sees nothing but a page saying so.

## Consequences

- Viewing and changing are separate, per area, for each person, and shown on one Admin page.
- Shared-read-access §3 kept profiles as named sets so that nobody holds a write tool nobody
  remembers granting. Switches reintroduce per-person differences, so they are held to the same
  question: each is a name from the fixed list (the table refuses anything else), the page shows
  them beside the profile they modify, and every change is recorded with who made it.
- An instance upgraded with Access configured and no owner named shows the claim page until the
  code from the log is entered, or `MERIDIAN_OWNER_EMAILS` is set.
- Without Access (a machine that is not exposed), `MERIDIAN_ADMIN_ALLOW_ANONYMOUS` still opens
  Admin, now as an owner; reader pages stay open as before.
- MCP tokens keep their profile's tool set; switches apply to the web.

## Alternatives considered

- **More profiles instead of switches.** Rejected: the operator asked for per-person control,
  and every new combination would need a release.
- **The first person to sign in becomes owner.** Rejected: whoever loads the page first wins, and
  a rebuilt database would hand ownership to the next visitor.
- **Permissions kept in Cloudflare Access groups.** Rejected: editing them needs the Cloudflare
  dashboard, not Meridian, and the API would still need the mapping from group to permission.
