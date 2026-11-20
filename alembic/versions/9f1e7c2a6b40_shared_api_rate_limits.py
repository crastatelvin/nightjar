"""shared api rate limits

Revision ID: 9f1e7c2a6b40
Revises: 4b7e2c9d1a83
Create Date: 2026-11-20 09:15:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9f1e7c2a6b40"
down_revision: str | None = "4b7e2c9d1a83"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_rate_limits",
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    # Pruning deletes by age; the index keeps that sweep cheap.
    op.create_index("ix_api_rate_limits_updated_at", "api_rate_limits", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_api_rate_limits_updated_at", table_name="api_rate_limits")
    op.drop_table("api_rate_limits")
