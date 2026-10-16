from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID


@dataclass(frozen=True)
class AdminClassDTO:
    id: UUID
    fecha: date
    start_time: time
    end_time: time
    bloque: str
    max_capacity: int
    instructor_name: str | None
    status: str
    cancel_reason: str | None
    reservados: int
    lista_espera: int


@dataclass(frozen=True)
class CierreDTO:
    id: UUID
    fecha: date
    tipo: str
    motivo: str | None
    creado_por: UUID
    creado_en: datetime


@dataclass(frozen=True)
class CalendarDayDTO:
    fecha: date
    es_dia_habil: bool
    cierre: CierreDTO | None
    clases: int
    reservas: int


@dataclass(frozen=True)
class GenerationResultDTO:
    fecha: date
    estado: str  # generada | cerrado
    clases_creadas: int
    clases_reabiertas: int
    motivo: str | None = None


@dataclass(frozen=True)
class StudentCalendarDayDTO:
    fecha: date
    hay_clases: bool
    tipo_cierre: str | None = None
    motivo: str | None = None
    etiqueta: str | None = None  # texto listo para mostrar, ej. "Feriado - Sin clases"
