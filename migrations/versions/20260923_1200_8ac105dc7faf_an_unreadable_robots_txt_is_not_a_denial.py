"""an unreadable robots.txt is not a denial

Task B-33, RFC 9309 §2.3.1.3. A robots.txt that cannot be read refuses its
origin until it can be, and that refusal was recorded as `robots_denied` —
which abandons the task. A DNS failure while the network came up after a
reboot therefore dropped URLs for good, each with an error saying the site had
disallowed them. `robots_unreachable` is retried instead.

Only the CHECK changes: the column is already `VARCHAR(21)`, wide enough for
the new value.

Revision ID: 8ac105dc7faf
Revises: c4e8b2a71d36
Create Date: 2026-09-23 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "8ac105dc7faf"
down_revision: str | Sequence[str] | None = "c4e8b2a71d36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The bare name: the metadata naming convention expands it to
# ck_fetch_attempts_fetch_outcome on both create and drop.
CONSTRAINT = "fetch_outcome"

OLD_OUTCOMES = (
    "success",
    "not_modified",
    "http_error",
    "timeout",
    "too_large",
    "robots_denied",
    "blocked",
    "connection_error",
    "parse_error",
    "unsafe_target",
    "content_type_rejected",
    "decompression_bomb",
    "too_many_redirects",
)

NEW_OUTCOMES = (*OLD_OUTCOMES, "robots_unreachable")


def _values(outcomes: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in outcomes)


def upgrade() -> None:
    # Hand-written: autogenerate does not see a CHECK change on an existing
    # table (P0-21), and would have emitted nothing at all.
    op.drop_constraint(CONSTRAINT, "fetch_attempts", type_="check")
    op.create_check_constraint(
        CONSTRAINT, "fetch_attempts", f"outcome IN ({_values(NEW_OUTCOMES)})"
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "fetch_attempts", type_="check")
    # Folded into the bucket these rows would have used before, rather than
    # deleted: they are refusals that really happened.
    op.execute(
        "UPDATE fetch_attempts SET outcome = 'robots_denied' WHERE outcome = 'robots_unreachable'"
    )
    op.create_check_constraint(
        CONSTRAINT, "fetch_attempts", f"outcome IN ({_values(OLD_OUTCOMES)})"
    )
