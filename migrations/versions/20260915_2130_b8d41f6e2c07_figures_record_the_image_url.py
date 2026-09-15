"""figures record the image url

Task P1-10. §6.6's table has `file_path` — a local path to a stored image — and
nothing in this system downloads figure images. So a figure row with a caption
and no URL describes a picture nobody can ever look at, and `P7-07`'s deferred
vision enrichment would have nothing to fetch.

Nullable, because a figure found in a PDF's text layer genuinely has no
addressable image: the caption is extractable and the picture is not.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d41f6e2c07"
down_revision: str | None = "a7c2e9d31b64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("figures", sa.Column("image_url", sa.Text(), nullable=True))
    # The figures panel and any enrichment batch both start from "which figures
    # does this source have", and a source's figures are always read together.
    op.create_index("ix_figures_source", "figures", ["source_id"])


def downgrade() -> None:
    op.drop_index("ix_figures_source", table_name="figures")
    op.drop_column("figures", "image_url")
