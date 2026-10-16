from abc import ABC, abstractmethod
from datetime import date
from uuid import UUID

from pilates.booking.application.dto import AttendanceClassDTO, GridRow, MyBookingDTO
from pilates.booking.domain.model import ClassSnapshot


class ClassCatalog(ABC):
    """Puerto hacia el contexto de agenda: lectura de clases como ClassSnapshot."""

    @abstractmethod
    def get(self, clase_id: UUID, *, for_update: bool = False) -> ClassSnapshot | None: ...


class BookingQueries(ABC):
    """Modelo de lectura (CQRS liviano) para pantallas de alumno y recepción."""

    @abstractmethod
    def grid_for_date(self, fecha: date, user_id: UUID) -> list[GridRow]: ...

    @abstractmethod
    def user_bookings(self, user_id: UUID) -> list[MyBookingDTO]: ...

    @abstractmethod
    def attendance_for_date(self, fecha: date) -> list[AttendanceClassDTO]: ...
