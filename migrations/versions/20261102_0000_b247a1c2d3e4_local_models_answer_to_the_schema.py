"""local models are held to the answer's schema while they write (task B-247)

`agents.structured`: `server` sends nothing, as before; `schema` sends extraction's and
tagging's answer schema with the request, for an OpenAI-compatible server that decodes to it
(llama.cpp turns it into a grammar). The seeded local rows become `schema`. Hosted rows stay
`server`: a hosted API may refuse an argument it does not know, or a schema it does not
support. A server that refuses it is asked again without it.

Revision ID: b247a1c2d3e4
Revises: b264a1c2d3e4
Create Date: 2026-11-02 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b247a1c2d3e4"
down_revision: str | None = "b264a1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LOCAL = ("local-llamacpp", "local-chat")


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "structured",
            sa.Enum(
                "server",
                "schema",
                name="agent_structured",
                native_enum=False,
                create_constraint=False,
            ),
            server_default="server",
            nullable=False,
        ),
    )
    op.create_check_constraint("agent_structured", "agents", "structured IN ('server', 'schema')")
    op.execute(
        sa.text("UPDATE agents SET structured = 'schema' WHERE agent_id IN :ids").bindparams(
            sa.bindparam("ids", value=LOCAL, expanding=True)
        )
    )


def downgrade() -> None:
    op.drop_constraint("agent_structured", "agents", type_="check")
    op.drop_column("agents", "structured")
