"""local models answer structured tasks without reasoning first (task B-259)

`agents.thinking`: `server` sends nothing (the server's default, as before), `off` asks an
OpenAI-compatible server not to reason before answering extraction, tagging and the other
structured tasks. The seeded local rows become `off`: measured on a Qwen3 27B, reasoning
first spent the whole answer budget and returned nothing, and answering directly gave
accepted relations in a tenth of the time. Hosted rows stay `server`: OpenAI's API refuses
arguments it does not know.

Revision ID: b259a1c2d3e4
Revises: b255a1c2d3e4
Create Date: 2026-11-01 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b259a1c2d3e4"
down_revision: str | None = "b255a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL = ("local-llamacpp", "local-chat")


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "thinking",
            sa.Enum(
                "server", "off", name="agent_thinking", native_enum=False, create_constraint=False
            ),
            server_default="server",
            nullable=False,
        ),
    )
    op.create_check_constraint("agent_thinking", "agents", "thinking IN ('server', 'off')")
    op.execute(
        sa.text("UPDATE agents SET thinking = 'off' WHERE agent_id IN :ids").bindparams(
            sa.bindparam("ids", value=LOCAL, expanding=True)
        )
    )


def downgrade() -> None:
    op.drop_constraint("agent_thinking", "agents", type_="check")
    op.drop_column("agents", "thinking")
