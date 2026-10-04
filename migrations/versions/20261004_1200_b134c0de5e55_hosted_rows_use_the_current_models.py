"""hosted rows use the current Claude models (task B-134, ADR 0004)

The seeded hosted rows named the previous Opus and Sonnet. The seed is insert-only, so a
migration updates existing databases as well as `config/agents.yaml`. Only rows that still
hold the previously seeded string are changed: a model an operator chose is left alone.

Revision ID: b134c0de5e55
Revises: b116a0c3d4e5
Create Date: 2026-10-04 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b134c0de5e55"
down_revision: str | None = "b116a0c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: agent_id -> (previously seeded model, current model)
MODELS = {
    "hosted-frontier": ("claude-opus-5", "claude-opus-5-5"),
    "hosted-mid": ("claude-sonnet-5", "claude-sonnet-5-5"),
}


def _swap(direction: int) -> None:
    for agent_id, pair in MODELS.items():
        old, new = pair[::direction]
        op.execute(
            sa.text(
                "UPDATE agents SET model = :new WHERE agent_id = :id AND model = :old"
            ).bindparams(new=new, id=agent_id, old=old)
        )


def upgrade() -> None:
    _swap(1)


def downgrade() -> None:
    _swap(-1)
