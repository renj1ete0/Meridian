"""preprint servers are not peer reviewed (task B-233)

A `preprint` source tier. Both columns that hold a tier (`sources.source_tier`,
`grants.max_source_tier`) get it in their CHECK, rewritten by hand as autogenerate does
not see CHECKs on existing tables; `preprint` fits the columns' width.

The tier map in the global `fetch_policy` row is authoritative after first boot (§13.1),
so the preprint patterns are written into it here, and arXiv's `peer_reviewed` entries
come out: an exact entry beats any pattern. Patterns and the priority are added only where
absent, so an operator's own list is kept. Stored sources are not touched: lowering a tier
is `worker.retier --apply`, run by a person, because a tier never drops automatically.

Revision ID: b233a1c2d3e4
Revises: b262a1c2d3e4
Create Date: 2026-11-01 23:00:00.000000
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "b233a1c2d3e4"
down_revision: str | None = "b262a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BEFORE = ("peer_reviewed", "government", "institutional", "press", "informal")
AFTER = ("peer_reviewed", "preprint", "government", "institutional", "press", "informal")

#: Mirrors `patterns.preprint` and `priority_by_tier.preprint` in
#: `config/source_tiers.yaml`; a drift test holds them together.
PREPRINT_PATTERNS = [
    "*.arxiv.org",
    "*.ssrn.com",
    "*.osf.io",
    "*.biorxiv.org",
    "*.medrxiv.org",
    "*.chemrxiv.org",
    "*.techrxiv.org",
    "*.eartharxiv.org",
    "*.engrxiv.org",
    "*.preprints.org",
    "*.researchsquare.com",
]
PREPRINT_PRIORITY = 40

#: The `peer_reviewed` entries arXiv had before this revision.
ARXIV_EXACT = "arxiv.org"
ARXIV_PATTERN = "*.arxiv.org"

COLUMNS = (("sources", "source_tier"), ("grants", "max_source_tier"))


def _checks(tiers: Sequence[str]) -> None:
    values = ", ".join(f"'{tier}'" for tier in tiers)
    for table, column in COLUMNS:
        op.drop_constraint("source_tier", table, type_="check")
        op.create_check_constraint("source_tier", table, f"{column} IN ({values})")


def _edit_map(edit: Any) -> None:
    bind = op.get_bind()
    settings = bind.scalar(sa.text("SELECT settings FROM fetch_policy WHERE domain = '*'"))
    if not settings or not isinstance(settings.get("source_tiers"), dict):
        return
    edit(settings["source_tiers"])
    bind.execute(
        sa.text(
            "UPDATE fetch_policy SET settings = CAST(:settings AS jsonb) WHERE domain = '*'"
        ).bindparams(settings=json.dumps(settings))
    )


def _without(names: list[str] | None, name: str) -> list[str]:
    return [n for n in names or [] if n != name]


def _add_preprint(tiers: dict[str, Any]) -> None:
    exact = tiers.setdefault("exact", {})
    patterns = tiers.setdefault("patterns", {})
    exact["peer_reviewed"] = _without(exact.get("peer_reviewed"), ARXIV_EXACT)
    patterns["peer_reviewed"] = _without(patterns.get("peer_reviewed"), ARXIV_PATTERN)
    patterns.setdefault("preprint", PREPRINT_PATTERNS)
    tiers.setdefault("priority_by_tier", {}).setdefault("preprint", PREPRINT_PRIORITY)


def _remove_preprint(tiers: dict[str, Any]) -> None:
    exact = tiers.setdefault("exact", {})
    patterns = tiers.setdefault("patterns", {})
    patterns.pop("preprint", None)
    (tiers.get("priority_by_tier") or {}).pop("preprint", None)
    if ARXIV_EXACT not in (exact.get("peer_reviewed") or []):
        exact["peer_reviewed"] = [ARXIV_EXACT, *(exact.get("peer_reviewed") or [])]
    if ARXIV_PATTERN not in (patterns.get("peer_reviewed") or []):
        patterns["peer_reviewed"] = [*(patterns.get("peer_reviewed") or []), ARXIV_PATTERN]


def upgrade() -> None:
    _checks(AFTER)
    _edit_map(_add_preprint)


def downgrade() -> None:
    # Before this revision a preprint server's paper was `peer_reviewed`; it returns there
    # rather than failing the CHECK.
    op.execute("UPDATE sources SET source_tier = 'peer_reviewed' WHERE source_tier = 'preprint'")
    op.execute(
        "UPDATE grants SET max_source_tier = 'peer_reviewed' WHERE max_source_tier = 'preprint'"
    )
    _checks(BEFORE)
    _edit_map(_remove_preprint)
