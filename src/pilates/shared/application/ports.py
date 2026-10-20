from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any
from uuid import UUID


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """Fecha/hora actual, con zona horaria del estudio."""


class AuditLog(ABC):
    @abstractmethod
    def record(self, admin_id: UUID, action: str, details: dict[str, Any]) -> None: ...
