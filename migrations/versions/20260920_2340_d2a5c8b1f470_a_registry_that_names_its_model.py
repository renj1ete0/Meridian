"""a registry that names its model

Task P4-15, §11.3, §11.11. The seeded agent rows shipped with
`<fill in exact model string>` as the model and no `api_key_env_var` at all.
Both are the same shape of defect `P4-07` found: a configuration value that is
wrong in a way nothing reports, and whose symptom only appears at the moment
somebody needs it.

A placeholder model string would reach the provider and come back as a bad
request from a vendor, hours into a run. An absent `api_key_env_var` is
quieter still — the SDK falls back to its own default variable, so it works by
coincidence on a machine where that happens to be set and fails on the server
where it is not.

**A migration as well as the YAML**, for the reason `P4-07` recorded in
`docs/handover.md`: the seed is insert-only, so editing `config/agents.yaml`
fixes new installs and leaves every seeded database exactly as wrong.

**Only the rows that still hold the placeholder are touched.** An operator who
has already filled in a model has made a decision, and a migration that
overwrote it would be replacing a choice with a default.

Revision ID: d2a5c8b1f470
Revises: 70dbd9d6b14e
Create Date: 2026-09-20 23:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d2a5c8b1f470"
down_revision: str | Sequence[str] | None = "70dbd9d6b14e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: agent_id -> the model string that row should name.
MODELS = {
    "hosted-frontier": "claude-opus-5",
    "hosted-mid": "claude-sonnet-5",
}

KEY_VAR = "ANTHROPIC_API_KEY"


def upgrade() -> None:
    for agent_id, model in MODELS.items():
        op.execute(
            f"""
            UPDATE agents
               SET model = '{model}'
             WHERE agent_id = '{agent_id}'
               AND (model IS NULL OR model LIKE '<%')
            """
        )

    # Every Anthropic row reads its key from the same variable, and naming it
    # is what stops the SDK's own default being relied on by accident.
    op.execute(
        f"""
        UPDATE agents
           SET api_key_env_var = '{KEY_VAR}'
         WHERE provider = 'anthropic'
           AND (api_key_env_var IS NULL OR btrim(api_key_env_var) = '')
        """
    )


def downgrade() -> None:
    # The placeholder is not restored: putting `<fill in ...>` back would be
    # writing a value that was never a choice. The variable name is cleared,
    # since that column did not previously hold one.
    op.execute(
        f"""
        UPDATE agents
           SET api_key_env_var = NULL
         WHERE provider = 'anthropic' AND api_key_env_var = '{KEY_VAR}'
        """
    )
