"""add client-facing service times

Revision ID: b418d0b920ef
Revises: a7367f120317
Create Date: 2026-07-22 23:40:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b418d0b920ef"
down_revision: str | None = "a7367f120317"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "slot_holds",
        sa.Column("service_starts_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "slot_holds",
        sa.Column("service_ends_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "appointments",
        sa.Column("service_starts_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.add_column(
        "appointments",
        sa.Column("service_ends_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        op.f("ix_appointments_service_starts_at"),
        "appointments",
        ["service_starts_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_appointments_service_starts_at"), table_name="appointments")
    op.drop_column("appointments", "service_ends_at")
    op.drop_column("appointments", "service_starts_at")
    op.drop_column("slot_holds", "service_ends_at")
    op.drop_column("slot_holds", "service_starts_at")
