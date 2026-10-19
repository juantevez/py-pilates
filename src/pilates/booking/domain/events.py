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
    """Se liberó un lugar. ``por`` indica si canceló el alumno o el gimnasio; ``con_clase``, si fue
    porque el gimnasio canceló o cerró la clase entera (y no sólo este turno)."""

    turno_id: UUID
    user_id: UUID
    clase_id: UUID
    por: str
    motivo: str | None
    con_clase: bool = False
