from dataclasses import dataclass
from uuid import UUID

from pilates.shared.domain.events import DomainEvent


@dataclass(frozen=True, kw_only=True)
class TurnoReservado(DomainEvent):
    """``desde_lista_espera``: el sistema le asignó el lugar al primero de la lista de espera."""

    turno_id: UUID
    user_id: UUID
    clase_id: UUID
    desde_lista_espera: bool = False


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


@dataclass(frozen=True, kw_only=True)
class LugarLiberado(DomainEvent):
    """Se liberó un lugar a menos de ``cancel_min_notice`` de la clase: ya no se asigna
    automáticamente (el alumno no podría cancelarlo), se avisa a toda la lista de espera."""

    clase_id: UUID
