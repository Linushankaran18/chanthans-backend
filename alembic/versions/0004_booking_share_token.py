"""bookings.share_token for the read-only team share link

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("bookings") as batch:
        batch.add_column(sa.Column("share_token", sa.String(64), nullable=True))
        batch.create_index("ix_bookings_share_token", ["share_token"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("bookings") as batch:
        batch.drop_index("ix_bookings_share_token")
        batch.drop_column("share_token")
