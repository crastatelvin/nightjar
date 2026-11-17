"""incident reporter and approval requester

Revision ID: 2d94c6b1f0a7
Revises: 8c1f0a7d4e21
Create Date: 2026-11-17 09:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2d94c6b1f0a7"
down_revision: str | None = "8c1f0a7d4e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("reported_by", sa.String(length=100), nullable=False, server_default=""),
    )
    op.add_column(
        "approvals",
        sa.Column("requested_by", sa.String(length=100), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("approvals", "requested_by")
    op.drop_column("incidents", "reported_by")
