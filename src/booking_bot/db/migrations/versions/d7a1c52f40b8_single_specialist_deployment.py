"""single specialist deployment

Revision ID: d7a1c52f40b8
Revises: c30e09f8a6bd
Create Date: 2026-07-25 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7a1c52f40b8"
down_revision: str | None = "c30e09f8a6bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "services",
        sa.Column("config_key", sa.String(length=80), nullable=True),
    )
    op.create_unique_constraint(
        op.f("uq_services_business_id"),
        "services",
        ["business_id", "config_key"],
    )
    op.create_table(
        "specialist_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("master_id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint("id = 1", name=op.f("ck_specialist_profile_single_row")),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_specialist_profile_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["master_id"],
            ["masters.id"],
            name=op.f("fk_specialist_profile_master_id_masters"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_specialist_profile")),
        sa.UniqueConstraint(
            "business_id",
            name=op.f("uq_specialist_profile_business_id"),
        ),
        sa.UniqueConstraint(
            "master_id",
            name=op.f("uq_specialist_profile_master_id"),
        ),
    )
    op.execute(
        """
        INSERT INTO specialist_profile (id, business_id, master_id)
        SELECT 1, businesses.id, masters.id
        FROM businesses
        JOIN masters ON masters.business_id = businesses.id
        ORDER BY businesses.created_at, masters.created_at
        LIMIT 1
        """
    )


def downgrade() -> None:
    op.drop_table("specialist_profile")
    op.drop_constraint(op.f("uq_services_business_id"), "services", type_="unique")
    op.drop_column("services", "config_key")
