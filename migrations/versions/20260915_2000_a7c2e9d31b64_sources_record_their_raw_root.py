"""sources record their raw root

Task P1-45. `raw_file_path` is relative, and to what was written down nowhere.

Found by `P1-31`'s first real run: three sources dangled against `.devdata/raw`
and their files were sitting in `.devdata/containerraw`, put there by `P1-30`'s
containerised verification writing to its own bind mount. Nothing was lost. But
"the file is gone" and "you are looking under a different root" were the same
observation, and no amount of care could separate them.

Nullable, no backfill, no default. Rows written before this column genuinely do
not record their root, and guessing one — say, whatever `MERIDIAN_RAW_ROOT` is
set to during the migration — would turn "unknown" into a confident wrong answer
for exactly the rows the column exists to explain.

This does **not** change resolution. `raw_file_path` stays relative and
`MERIDIAN_RAW_ROOT` still resolves it; an absolute path in the table would bake
in a container's mount point and break the moment the store moved. The column
answers "which store was this written into", which is a different question.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c2e9d31b64"
down_revision: str | None = "f4b8c3e71a25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("raw_root", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "raw_root")
