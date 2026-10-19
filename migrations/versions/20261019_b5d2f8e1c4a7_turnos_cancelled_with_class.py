"""turnos.cancelled_with_class: el turno se canceló porque el gimnasio canceló o cerró la clase

Revision ID: b5d2f8e1c4a7
Revises: 7c1e4b9a2d3f
Create Date: 2026-10-19 17:45:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'b5d2f8e1c4a7'
down_revision: str | None = '7c1e4b9a2d3f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'turnos', sa.Column('cancelled_with_class', sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    # Hasta ahora el gimnasio sólo cancelaba turnos por clase/cierre o por cuenta suspendida.
    op.execute(
        "UPDATE turnos SET cancelled_with_class = true "
        "WHERE status = 'cancelada_por_admin' AND cancel_reason IS DISTINCT FROM 'Cuenta suspendida'"
    )


def downgrade() -> None:
    op.drop_column('turnos', 'cancelled_with_class')
