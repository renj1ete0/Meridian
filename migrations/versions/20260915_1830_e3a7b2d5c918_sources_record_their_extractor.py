"""sources record their extractor

Task P1-44. §6.6 routes each format to a different tool and HTML to two of them,
so "how was this read" is a per-row fact that cannot be derived from the media
type.

Nullable with no backfill and no default: the sources already in a corpus were
extracted before anything recorded it, and inventing a value for them would
assert something nobody knows. NULL means "extracted before this column
existed", which is the truth and is distinguishable from every real extractor
name.

Plain Text rather than a constrained value set — see the model for why a CHECK
here would recreate `P1-28`'s failure mode.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3a7b2d5c918"
down_revision: str | None = "d2f691c4a7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("extractor", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "extractor")
