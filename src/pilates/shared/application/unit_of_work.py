from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pilates.shared.application.ports import EventBus
from pilates.shared.domain.aggregate import AggregateRoot
from pilates.shared.domain.events import DomainEvent

if TYPE_CHECKING:
    from pilates.booking.application.ports import BookingQueries, ClassCatalog
    from pilates.booking.domain.repositories import TurnoRepository, WaitlistRepository
    from pilates.identity.application.ports import UserQueries
    from pilates.identity.domain.repositories import PasswordResetTokenRepository, UserRepository
    from pilates.scheduling.application.ports import ScheduleQueries
    from pilates.scheduling.domain.repositories import CierreRepository, ClaseRepository
    from pilates.shared.application.ports import AuditLog


class UnitOfWork(ABC):
    """Límite transaccional compartido por los bounded contexts del monolito modular.

    Los repositorios registran los agregados que tocan (``track``); tras un commit
    exitoso se recolectan sus eventos de dominio y se publican en el EventBus.
    """

    users: UserRepository
    reset_tokens: PasswordResetTokenRepository
    user_queries: UserQueries
    clases: ClaseRepository
    cierres: CierreRepository
    schedule_queries: ScheduleQueries
    turnos: TurnoRepository
    waitlist: WaitlistRepository
    class_catalog: ClassCatalog
    booking_queries: BookingQueries
    audit: AuditLog

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._seen: list[AggregateRoot] = []
        self._extra_events: list[DomainEvent] = []

    def track(self, aggregate: AggregateRoot) -> None:
        if not any(a is aggregate for a in self._seen):
            self._seen.append(aggregate)

    def add_event(self, event: DomainEvent) -> None:
        """Evento de aplicación que no pertenece a un agregado puntual (se publica tras el commit)."""
        self._extra_events.append(event)

    def __enter__(self) -> UnitOfWork:
        self._seen = []
        self._extra_events = []
        return self

    def __exit__(self, *exc) -> None:
        self.rollback()

    def commit(self) -> None:
        self._commit()
        events = [e for agg in self._seen for e in agg.pull_events()] + self._extra_events
        self._seen, self._extra_events = [], []
        if events:
            self._event_bus.publish(events)

    @abstractmethod
    def _commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...
