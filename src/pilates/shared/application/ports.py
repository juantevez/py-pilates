from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID

from pilates.shared.domain.events import DomainEvent


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """Fecha/hora actual, con zona horaria del estudio."""


class EventBus(ABC):
    @abstractmethod
    def publish(self, events: list[DomainEvent]) -> None: ...


class AuditLog(ABC):
    @abstractmethod
    def record(self, admin_id: UUID, action: str, details: dict[str, Any]) -> None: ...
