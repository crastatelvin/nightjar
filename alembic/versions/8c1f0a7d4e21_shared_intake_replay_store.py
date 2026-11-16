"""shared intake replay store

Revision ID: 8c1f0a7d4e21
Revises: 0f76e155f27b
Create Date: 2026-11-16 10:12:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c1f0a7d4e21"
down_revision: str | None = "0f76e155f27b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "intake_deliveries",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("delivery_id", sa.String(length=200), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "delivery_id"),
    )
    op.create_index(
        op.f("ix_intake_deliveries_seen_at"), "intake_deliveries", ["seen_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_intake_deliveries_seen_at"), table_name="intake_deliveries")
    op.drop_table("intake_deliveries")
