"""outbox: eventos de dominio persistidos en la misma transacción que los cambios

Revision ID: e3a9c7d1f5b2
Revises: b5d2f8e1c4a7
Create Date: 2026-10-20 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'e3a9c7d1f5b2'
down_revision: str | None = 'b5d2f8e1c4a7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('outbox',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('event_name', sa.String(length=100), nullable=False),
    sa.Column('handler_name', sa.String(length=100), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('last_relayed_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_outbox_pending', 'outbox', ['created_at'], unique=False,
                    postgresql_where=sa.text('processed_at IS NULL'))


def downgrade() -> None:
    op.drop_index('ix_outbox_pending', table_name='outbox', postgresql_where=sa.text('processed_at IS NULL'))
    op.drop_table('outbox')
