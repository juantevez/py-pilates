"""sesiones: un registro por login para poder revocar tokens antes de que venzan

Revision ID: a8c4e2f6b1d9
Revises: e3a9c7d1f5b2
Create Date: 2026-10-20 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'a8c4e2f6b1d9'
down_revision: str | None = 'e3a9c7d1f5b2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('sesiones',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoke_reason', sa.String(length=50), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_sesiones_user_id'), 'sesiones', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_sesiones_user_id'), table_name='sesiones')
    op.drop_table('sesiones')
