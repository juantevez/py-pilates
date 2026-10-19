from abc import ABC, abstractmethod
from datetime import date, datetime
from uuid import UUID

from pilates.booking.domain.model import Turno, WaitlistEntry


class TurnoRepository(ABC):
    @abstractmethod
    def add(self, turno: Turno) -> None: ...

    @abstractmethod
    def save(self, turno: Turno) -> None: ...

    @abstractmethod
    def get(self, turno_id: UUID) -> Turno | None: ...

    @abstractmethod
    def get_by_user_and_clase(self, user_id: UUID, clase_id: UUID) -> Turno | None: ...

    @abstractmethod
    def count_active(self, clase_id: UUID) -> int: ...

    @abstractmethod
    def list_active_by_clase(self, clase_id: UUID) -> list[Turno]: ...

    @abstractmethod
    def list_cancelled_with_class(self, clase_id: UUID) -> list[Turno]: ...

    @abstractmethod
    def list_active_by_user_on_date(self, user_id: UUID, fecha: date) -> list[Turno]: ...

    @abstractmethod
    def list_active_by_user_from(self, user_id: UUID, desde: datetime) -> list[Turno]:
        """Turnos confirmados del usuario cuya clase empieza a partir de ``desde``."""

    @abstractmethod
    def list_pending_reminders(self, desde: datetime, hasta: datetime) -> list[Turno]:
        """Turnos confirmados, sin recordatorio enviado, de clases activas que empiezan en [desde, hasta]."""


class WaitlistRepository(ABC):
    @abstractmethod
    def add(self, entry: WaitlistEntry) -> None: ...

    @abstractmethod
    def save(self, entry: WaitlistEntry) -> None: ...

    @abstractmethod
    def delete(self, entry: WaitlistEntry) -> None: ...

    @abstractmethod
    def get(self, user_id: UUID, clase_id: UUID) -> WaitlistEntry | None: ...

    @abstractmethod
    def list_by_clase(self, clase_id: UUID) -> list[WaitlistEntry]: ...

    @abstractmethod
    def delete_by_clase(self, clase_id: UUID) -> None: ...
