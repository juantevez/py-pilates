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
    """La próxima fecha hábil está cerrada (feriado, mantenimiento, etc.)."""

    fecha: date
    motivo: str
    proximo_dia_habil: date


@dataclass(frozen=True, kw_only=True)
class ClasesReabiertas(DomainEvent):
    """Clases que vuelven a estar disponibles (reactivación o cierre eliminado).

    Se avisa a los alumnos cuyo turno había cancelado el gimnasio para que vuelvan a reservar.
    """

    clase_ids: tuple[UUID, ...]
