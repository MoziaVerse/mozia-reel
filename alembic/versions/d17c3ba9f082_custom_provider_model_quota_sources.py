"""custom_provider_model 增加 quota_sources

Revision ID: d17c3ba9f082
Revises: b64490270183
Create Date: 2026-08-28 11:20:14.882031

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d17c3ba9f082"
down_revision: str | Sequence[str] | None = "b64490270183"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.add_column(sa.Column("quota_sources", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("custom_provider_model", schema=None) as batch_op:
        batch_op.drop_column("quota_sources")
