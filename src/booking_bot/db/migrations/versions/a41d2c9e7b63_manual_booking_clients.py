"""manual booking clients

Revision ID: a41d2c9e7b63
Revises: f9135a4b8c72
Create Date: 2026-07-25 19:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a41d2c9e7b63"
down_revision: str | None = "f9135a4b8c72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "telegram_users",
        "telegram_user_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        """
        WITH numbered AS (
            SELECT id, row_number() OVER (ORDER BY created_at, id) AS row_number
            FROM telegram_users
            WHERE telegram_user_id IS NULL
        )
        UPDATE telegram_users AS users
        SET telegram_user_id = 9223372036854775807 - numbered.row_number
        FROM numbered
        WHERE users.id = numbered.id
        """
    )
    op.alter_column(
        "telegram_users",
        "telegram_user_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
