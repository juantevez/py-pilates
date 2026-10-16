from dataclasses import dataclass, field
from datetime import date, datetime, time
from uuid import UUID


@dataclass(frozen=True)
class TurnoDTO:
    id: UUID
    clase_id: UUID
    status: str
    booked_at: datetime
    cancelled_at: datetime | None


@dataclass(frozen=True)
class GridRow:
    clase_id: UUID
    fecha: date
    start_time: time
    end_time: time
    bloque: str
    instructor_name: str | None
    capacidad: int
    status: str
    reservados: int
    lista_espera: int
    mi_turno_id: UUID | None
    en_lista_espera: bool


@dataclass(frozen=True)
class ClassAvailabilityDTO:
    clase_id: UUID
    fecha: date
    start_time: time
    end_time: time
    bloque: str
    instructor_name: str | None
    capacidad: int
    reservados: int
    disponibles: int
    completa: bool
    status: str
    mi_turno_id: UUID | None
    en_lista_espera: bool
    puede_reservar: bool
    puede_cancelar: bool
    puede_anotarse_lista_espera: bool


@dataclass(frozen=True)
class DayAvailabilityDTO:
    fecha: date
    estado: str  # abierta | pendiente_apertura | cerrado | sin_clases
    apertura: datetime | None = None
    motivo_cierre: str | None = None
    proximo_dia_habil: date | None = None
    clases: list[ClassAvailabilityDTO] = field(default_factory=list)


@dataclass(frozen=True)
class MyBookingDTO:
    turno_id: UUID
    clase_id: UUID
    fecha: date
    start_time: time
    end_time: time
    instructor_name: str | None
    clase_status: str
    status: str
    booked_at: datetime
    cancelled_at: datetime | None
    cancel_reason: str | None
    puede_cancelar: bool = False


@dataclass(frozen=True)
class MyBookingsDTO:
    proximas: list[MyBookingDTO]
    pasadas: list[MyBookingDTO]


@dataclass(frozen=True)
class AttendeeDTO:
    turno_id: UUID
    user_id: UUID
    full_name: str
    email: str
    status: str


@dataclass(frozen=True)
class AttendanceClassDTO:
    clase_id: UUID
    fecha: date
    start_time: time
    end_time: time
    status: str
    capacidad: int
    alumnos: list[AttendeeDTO]
