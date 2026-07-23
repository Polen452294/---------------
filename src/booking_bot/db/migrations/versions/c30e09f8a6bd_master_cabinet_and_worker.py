"""master cabinet and notification worker support

Revision ID: c30e09f8a6bd
Revises: b418d0b920ef
Create Date: 2026-07-23 13:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c30e09f8a6bd"
down_revision: str | None = "b418d0b920ef"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        op.f("uq_masters_business_id"),
        "masters",
        ["business_id", "user_id"],
    )
    op.create_table(
        "master_invites",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("master_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_master_invites_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["master_id"],
            ["masters.id"],
            name=op.f("fk_master_invites_master_id_masters"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_master_invites")),
        sa.UniqueConstraint("code_hash", name=op.f("uq_master_invites_code_hash")),
    )
    op.create_index(
        op.f("ix_master_invites_business_id"),
        "master_invites",
        ["business_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_master_invites_expires_at"),
        "master_invites",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_master_invites_master_id"),
        "master_invites",
        ["master_id"],
        unique=False,
    )
    op.create_index(
        "ix_notification_jobs_state_scheduled_for",
        "notification_jobs",
        ["state", "scheduled_for"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_jobs_state_scheduled_for",
        table_name="notification_jobs",
    )
    op.drop_index(op.f("ix_master_invites_master_id"), table_name="master_invites")
    op.drop_index(op.f("ix_master_invites_expires_at"), table_name="master_invites")
    op.drop_index(op.f("ix_master_invites_business_id"), table_name="master_invites")
    op.drop_table("master_invites")
    op.drop_constraint(op.f("uq_masters_business_id"), "masters", type_="unique")
