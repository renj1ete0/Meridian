"""a relay agent for an attended model

Task P4-18. `config/agents.yaml` gains `claude-code-session`, a `relay` agent
that exchanges prompts and answers through a directory rather than a network.
The seed is insert-only at first boot (§13.1), so a database that already
exists would never see the row; this writes it, **disabled**, and only where
no row of that id exists. Enabling it is an operator's decision in Admin — a
relay agent enabled with nobody answering defers every run.

Revision ID: 5d2e9f1a7c30
Revises: 8ac105dc7faf
Create Date: 2026-09-23 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "5d2e9f1a7c30"
down_revision: str | Sequence[str] | None = "8ac105dc7faf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO agents (
            agent_id, provider, model, task_types, token_scope, cost_tier,
            quality_tier, max_context, enabled, availability
        )
        VALUES (
            'claude-code-session', 'relay', 'claude-opus-5-5',
            ARRAY['relation_extraction', 'tag_attributes'], 'read_write', 'free',
            4, 200000, false, 'on_demand'
        )
        ON CONFLICT (agent_id) DO NOTHING
        """
    )


def downgrade() -> None:
    # Only while it is still the row this migration wrote and nothing ran under
    # it: an agent with runs is provenance for whatever it produced.
    op.execute(
        """
        DELETE FROM agents
        WHERE agent_id = 'claude-code-session'
          AND provider = 'relay'
          AND NOT EXISTS (SELECT 1 FROM runs WHERE runs.agent_id = 'claude-code-session')
        """
    )
