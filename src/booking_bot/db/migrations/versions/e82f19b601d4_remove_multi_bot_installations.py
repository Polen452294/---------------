"""remove multi-bot installations

Revision ID: e82f19b601d4
Revises: d7a1c52f40b8
Create Date: 2026-07-25 12:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e82f19b601d4"
down_revision: str | None = "d7a1c52f40b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("bot_installations")


def downgrade() -> None:
    op.create_table(
        "bot_installations",
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_bot_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64)),
        sa.Column("token_ciphertext", sa.Text(), nullable=False),
        sa.Column("webhook_path_secret", sa.String(length=64), nullable=False),
        sa.Column("webhook_header_secret_hash", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
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
            name=op.f("fk_bot_installations_business_id_businesses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bot_installations")),
        sa.UniqueConstraint(
            "telegram_bot_id",
            name=op.f("uq_bot_installations_telegram_bot_id"),
        ),
        sa.UniqueConstraint(
            "webhook_path_secret",
            name=op.f("uq_bot_installations_webhook_path_secret"),
        ),
    )
    op.create_index(
        op.f("ix_bot_installations_business_id"),
        "bot_installations",
        ["business_id"],
        unique=False,
    )
