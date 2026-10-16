from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, tzinfo
from enum import StrEnum
from uuid import UUID, uuid4

from pilates.scheduling.domain.calendar import GridTemplate, Slot, TimeBlock, is_weekday
from pilates.shared.domain.aggregate import AggregateRoot
from pilates.shared.domain.errors import BusinessRuleViolation, ConflictError


class ClaseStatus(StrEnum):
    ACTIVA = "activa"
    CANCELADA = "cancelada"
    FERIADO = "feriado"


class TipoCierre(StrEnum):
    FERIADO_NACIONAL = "feriado_nacional"
    VACACIONES_GYM = "vacaciones_gym"
    ENFERMEDAD_INSTRUCTOR = "enfermedad_instructor"
    MANTENIMIENTO = "mantenimiento"
    OTRO = "otro"


@dataclass(eq=False, kw_only=True)
class Clase(AggregateRoot):
    id: UUID
    fecha: date
    start_time: time
    end_time: time
    bloque: TimeBlock
    max_capacity: int
    status: ClaseStatus
    instructor_name: str | None = None
    cancel_reason: str | None = None

    @classmethod
    def schedule(cls, *, fecha: date, slot: Slot, grid: GridTemplate, instructor_name: str | None = None) -> Clase:
        if not is_weekday(fecha):
            raise BusinessRuleViolation("Sólo hay clases de lunes a viernes", code="not_a_weekday")
        return cls(
            id=uuid4(),
            fecha=fecha,
            start_time=slot.start,
            end_time=grid.end_of(slot.start),
            bloque=slot.block,
            max_capacity=grid.default_capacity,
            status=ClaseStatus.ACTIVA,
            instructor_name=instructor_name,
        )

    def starts_at(self, tz: tzinfo | None) -> datetime:
        return datetime.combine(self.fecha, self.start_time, tzinfo=tz)

    @property
    def is_active(self) -> bool:
        return self.status is ClaseStatus.ACTIVA

    def cancel(self, motivo: str) -> None:
        if self.status is ClaseStatus.CANCELADA:
            raise ConflictError("La clase ya está cancelada", code="class_already_cancelled")
        self.status = ClaseStatus.CANCELADA
        self.cancel_reason = motivo

    def reactivate(self) -> None:
        """Deshace una cancelación puntual. Las clases cerradas por un cierre se reabren borrando el cierre."""
        if self.status is ClaseStatus.FERIADO:
            raise BusinessRuleViolation(
                "La clase está cerrada por un cierre del día: eliminá el cierre para reabrirla",
                code="class_closed_by_closure",
            )
        if self.status is not ClaseStatus.CANCELADA:
            raise ConflictError("La clase ya está activa", code="class_already_active")
        self.status = ClaseStatus.ACTIVA
        self.cancel_reason = None

    def close_for_closure(self, motivo: str) -> None:
        """El día quedó bloqueado por un cierre del gimnasio."""
        if self.status is ClaseStatus.ACTIVA:
            self.status = ClaseStatus.FERIADO
            self.cancel_reason = motivo

    def reopen(self) -> None:
        """Se eliminó el cierre del día: sólo se reabren las clases cerradas por cierre."""
        if self.status is ClaseStatus.FERIADO:
            self.status = ClaseStatus.ACTIVA
            self.cancel_reason = None

    def change_capacity(self, new_capacity: int, active_bookings: int) -> None:
        if new_capacity < 1:
            raise BusinessRuleViolation("La capacidad debe ser al menos 1", code="invalid_capacity")
        if new_capacity < active_bookings:
            raise BusinessRuleViolation(
                f"Ya hay {active_bookings} reservas activas; no se puede bajar la capacidad a {new_capacity}",
                code="capacity_below_bookings",
            )
        self.max_capacity = new_capacity

    def assign_instructor(self, name: str | None) -> None:
        self.instructor_name = (name or "").strip() or None


@dataclass(eq=False, kw_only=True)
class CierreGimnasio(AggregateRoot):
    id: UUID
    fecha: date
    tipo: TipoCierre
    motivo: str | None
    creado_por: UUID
    creado_en: datetime

    @classmethod
    def create(cls, *, fecha: date, tipo: TipoCierre, motivo: str | None, creado_por: UUID, now: datetime) -> CierreGimnasio:
        return cls(
            id=uuid4(),
            fecha=fecha,
            tipo=tipo,
            motivo=(motivo or "").strip() or None,
            creado_por=creado_por,
            creado_en=now,
        )

    def update(self, *, tipo: TipoCierre, motivo: str | None) -> None:
        self.tipo = tipo
        self.motivo = (motivo or "").strip() or None

    @property
    def descripcion(self) -> str:
        return self.motivo or self.tipo.value.replace("_", " ").capitalize()
