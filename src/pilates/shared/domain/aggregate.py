from dataclasses import dataclass, field

from pilates.shared.domain.events import DomainEvent


@dataclass(eq=False, kw_only=True)
class AggregateRoot:
    """Raíz de agregado: acumula eventos de dominio hasta que la UoW los publica."""

    _events: list[DomainEvent] = field(default_factory=list, init=False, repr=False)

    def record(self, event: DomainEvent) -> None:
        self._events.append(event)

    def pull_events(self) -> list[DomainEvent]:
        events, self._events = self._events, []
        return events
