"""bookings, google_calendar_integrations, users.full_name

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-02
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("full_name", sa.String(255), nullable=True))

    op.create_table(
        "bookings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("booking_number", sa.String(32), nullable=False),
        sa.Column("customer_name", sa.String(255), nullable=False),
        sa.Column("customer_email", sa.String(320), nullable=True),
        sa.Column("customer_phone", sa.String(50), nullable=False),
        sa.Column("service_type", sa.String(100), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("location", sa.String(500), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("deposit_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("delivery_due_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("google_calendar_event_id", sa.String(255), nullable=True),
        sa.Column("calendar_sync_status", sa.String(16), nullable=False),
        sa.Column("calendar_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calendar_sync_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_bookings"),
    )
    op.create_index("ix_bookings_booking_number", "bookings", ["booking_number"], unique=True)
    op.create_index("ix_bookings_event_date", "bookings", ["event_date"])
    op.create_index("ix_bookings_status", "bookings", ["status"])

    op.create_table(
        "google_calendar_integrations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("google_email", sa.String(320), nullable=False),
        sa.Column("calendar_id", sa.String(255), nullable=False),
        sa.Column("access_token_encrypted", sa.Text(), nullable=False),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=False),
        sa.Column("token_expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("is_connected", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_google_calendar_integrations_user_id_users"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_google_calendar_integrations"),
        sa.UniqueConstraint("user_id", name="uq_google_calendar_integrations_user_id"),
    )


def downgrade() -> None:
    op.drop_table("google_calendar_integrations")
    op.drop_index("ix_bookings_status", table_name="bookings")
    op.drop_index("ix_bookings_event_date", table_name="bookings")
    op.drop_index("ix_bookings_booking_number", table_name="bookings")
    op.drop_table("bookings")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("full_name")
