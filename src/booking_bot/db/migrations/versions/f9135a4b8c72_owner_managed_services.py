"""owner managed services

Revision ID: f9135a4b8c72
Revises: e82f19b601d4
Create Date: 2026-07-25 13:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f9135a4b8c72"
down_revision: str | None = "e82f19b601d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "services",
        sa.Column(
            "is_owner_managed",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("services", "is_owner_managed")
