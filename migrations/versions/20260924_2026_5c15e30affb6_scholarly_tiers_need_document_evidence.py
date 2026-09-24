"""scholarly tiers need document evidence (task B-50)

Data only. Adds `needs_scholarly_evidence` to the seeded tier map in the global
`fetch_policy` row — the database is authoritative for it after first boot
(§13.1), so editing `config/source_tiers.yaml` alone would change nothing on a
running install. Only where the key is absent: an operator who has set it keeps
their list.

Revision ID: 5c15e30affb6
Revises: ff9f8eafb699
Create Date: 2026-09-24 20:26:48.062975
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c15e30affb6"
down_revision: str | None = "ff9f8eafb699"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Mirrors `needs_scholarly_evidence` in `config/source_tiers.yaml`; a drift test
#: holds the two together.
EVIDENCE_PATTERNS = ["*.edu", "*.ac.uk", "*.ac.jp", "*.ac.kr", "*.edu.sg", "*.edu.au", "*.edu.hk"]


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE fetch_policy
               SET settings = jsonb_set(
                       settings, '{source_tiers,needs_scholarly_evidence}',
                       CAST(:patterns AS jsonb))
             WHERE domain = '*'
               AND settings ? 'source_tiers'
               AND NOT (settings -> 'source_tiers' ? 'needs_scholarly_evidence')
            """
        ).bindparams(patterns=json.dumps(EVIDENCE_PATTERNS))
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE fetch_policy
           SET settings = settings #- '{source_tiers,needs_scholarly_evidence}'
         WHERE domain = '*'
        """
    )
