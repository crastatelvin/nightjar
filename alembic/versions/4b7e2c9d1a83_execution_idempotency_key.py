"""execution idempotency key

Revision ID: 4b7e2c9d1a83
Revises: 2d94c6b1f0a7
Create Date: 2026-11-17 10:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "4b7e2c9d1a83"
down_revision: str | None = "2d94c6b1f0a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("idempotency_key", sa.String(length=64), nullable=False, server_default=""),
    )
    # Partial: a failed attempt must stay retryable, so only one *successful*
    # execution may exist per key. Rows without a key (manual steps, history)
    # are excluded.
    op.create_index(
        "uq_executions_idempotency",
        "executions",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("status = 'executed' AND idempotency_key <> ''"),
    )


def downgrade() -> None:
    op.drop_index("uq_executions_idempotency", table_name="executions")
    op.drop_column("executions", "idempotency_key")
