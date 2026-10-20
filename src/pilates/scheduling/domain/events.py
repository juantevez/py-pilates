from dataclasses import dataclass
from datetime import date
from uuid import UUID

from pilates.shared.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class TurnosHabilitados(DomainEvent):
    """Apertura nocturna: ya se pueden reservar los turnos de ``fecha``."""

    fecha: date


@dataclass(frozen=True, kw_only=True)
class DiaSinClases(DomainEvent):
    """Una fecha hábil queda cerrada (feriado, mantenimiento, etc.): se avisa a los alumnos.

    ``excluir_user_ids``: alumnos que ya reciben el aviso de cancelación de su turno ese día.
    """

    fecha: date
    motivo: str
    proximo_dia_habil: date
    excluir_user_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ClasesReabiertas(DomainEvent):
    """Clases que vuelven a estar disponibles (reactivación o cierre eliminado).

    Se avisa a los alumnos cuyo turno había cancelado el gimnasio para que vuelvan a reservar.
    """

    clase_ids: tuple[UUID, ...]
