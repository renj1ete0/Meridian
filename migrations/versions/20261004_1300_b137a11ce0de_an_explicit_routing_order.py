"""an explicit routing order, and a hosted OpenAI-compatible row (task B-137, ADR 0002)

Adds `agents.route_order`. Rows with an order are tried first, lowest first; rows without
one keep routing by quality tier, so a registry that sets none routes as before.

The seed is insert-only, so existing databases get the seeded orders here, and the new
`hosted-compatible` row if it is missing. Only values still at their seeded state are
changed: an order or task list the operator set is left alone.

Revision ID: b137a11ce0de
Revises: b134c0de5e55
Create Date: 2026-10-04 13:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b137a11ce0de"
down_revision: str | None = "b134c0de5e55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDERS = {
    "local-llamacpp": 10,
    "local-chat": 10,
    "hosted-compatible": 20,
    "hosted-frontier": 30,
    "hosted-mid": 30,
    "claude-code-session": 40,
}

HOSTED_COMPATIBLE = {
    "agent_id": "hosted-compatible",
    "provider": "openai_compatible",
    "model": "${HOSTED_LLM_MODEL}",
    "api_key_env_var": "HOSTED_LLM_API_KEY",
    "task_types": [
        "relation_extraction",
        "tag_attributes",
        "gap_analysis",
        "analogical_expansion",
        "drafting",
        "chat",
    ],
    "token_scope": "read_write",
    "cost_tier": "medium",
    "quality_tier": 3,
    "enabled": False,
    "route_order": 20,
    "endpoint": "${HOSTED_LLM_URL}",
    "availability": "on_demand",
}


def upgrade() -> None:
    op.add_column("agents", sa.Column("route_order", sa.Integer(), nullable=True))

    columns = ", ".join(HOSTED_COMPATIBLE)
    values = ", ".join(f":{name}" for name in HOSTED_COMPATIBLE)
    op.execute(
        sa.text(
            f"INSERT INTO agents ({columns}) VALUES ({values}) ON CONFLICT (agent_id) DO NOTHING"
        ).bindparams(
            sa.bindparam("task_types", type_=sa.ARRAY(sa.Text())),
            **{k: v for k, v in HOSTED_COMPATIBLE.items() if k != "task_types"},
            task_types=HOSTED_COMPATIBLE["task_types"],
        )
    )
    for agent_id, order in ORDERS.items():
        op.execute(
            sa.text(
                "UPDATE agents SET route_order = :order "
                "WHERE agent_id = :id AND route_order IS NULL"
            ).bindparams(order=order, id=agent_id)
        )
    # The local row is first for synthesis only if it may extract; added only while the row is
    # still the seeded placeholder, i.e. nobody has configured it yet.
    op.execute(
        "UPDATE agents SET task_types = array_prepend('relation_extraction', task_types) "
        "WHERE agent_id = 'local-llamacpp' AND model LIKE '<%' "
        "AND NOT ('relation_extraction' = ANY (task_types))"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE agents SET task_types = array_remove(task_types, 'relation_extraction') "
        "WHERE agent_id = 'local-llamacpp' AND model LIKE '<%'"
    )
    op.execute("DELETE FROM agents WHERE agent_id = 'hosted-compatible' AND NOT enabled")
    op.drop_column("agents", "route_order")
