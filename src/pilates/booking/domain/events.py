from dataclasses import dataclass
from uuid import UUID

from pilates.shared.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class TurnoReservado(DomainEvent):
    turno_id: UUID
    user_id: UUID
    clase_id: UUID


@dataclass(frozen=True, kw_only=True)
class TurnoCancelado(DomainEvent):
    """Se liberó un lugar. ``por`` indica si canceló el alumno o el gimnasio."""

    turno_id: UUID
    user_id: UUID
    clase_id: UUID
    por: str
    motivo: str | None
