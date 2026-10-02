"""create users and hero_images

Revision ID: 0001
Revises:
Create Date: 2026-10-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "hero_images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("image_key", sa.String(512), nullable=False),
        sa.Column("image_url", sa.String(1024), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("subtitle", sa.String(500), nullable=True),
        sa.Column("alt_text", sa.String(500), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_hero_images"),
    )


def downgrade() -> None:
    op.drop_table("hero_images")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
