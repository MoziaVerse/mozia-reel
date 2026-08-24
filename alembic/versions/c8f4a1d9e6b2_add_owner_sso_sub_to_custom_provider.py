"""add owner_sso_sub to custom_provider

Revision ID: c8f4a1d9e6b2
Revises: b3f9c07ae214
Create Date: 2026-08-24 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8f4a1d9e6b2"
down_revision: str | Sequence[str] | None = "b3f9c07ae214"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema.

    Additive nullable tenant fingerprint. No backfill — existing rows stay
    NULL until the next matrix handshake writes it (seed_gateway_provider
    sets it on both create and update-existing branches), and it never
    participates in any WHERE/filter — it is a defense-in-depth check read
    at backend-load time, not a routing key.
    """
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_sso_sub", sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("custom_provider", schema=None) as batch_op:
        batch_op.drop_column("owner_sso_sub")
