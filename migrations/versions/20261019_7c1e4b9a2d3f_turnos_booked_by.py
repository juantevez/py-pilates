"""turnos.booked_by: quién creó la reserva (alumno o gimnasio)

Revision ID: 7c1e4b9a2d3f
Revises: 04edcad12a0b
Create Date: 2026-10-19 17:30:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '7c1e4b9a2d3f'
down_revision: str | None = '04edcad12a0b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'turnos',
        sa.Column(
            'booked_by',
            sa.Enum('alumno', 'gimnasio', name='reservado_por', native_enum=False, create_constraint=True, length=32),
            server_default='alumno',
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column('turnos', 'booked_by')
