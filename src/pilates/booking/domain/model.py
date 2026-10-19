from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pilates.booking.domain.events import TurnoCancelado, TurnoReservado
from pilates.shared.domain.aggregate import AggregateRoot
from pilates.shared.domain.errors import BusinessRuleViolation, ConflictError


class TurnoStatus(StrEnum):
    CONFIRMADA = "confirmada"
    CANCELADA = "cancelada"
    CANCELADA_POR_ADMIN = "cancelada_por_admin"
    ASISTIO = "asistio"
    NO_ASISTIO = "no_asistio"


class CanceladoPor(StrEnum):
    ALUMNO = "alumno"
    GIMNASIO = "gimnasio"


class ReservadoPor(StrEnum):
    ALUMNO = "alumno"
    GIMNASIO = "gimnasio"


@dataclass(frozen=True)
class ClassSnapshot:
    """Vista de una clase desde el contexto de reservas (anti-corruption layer)."""

    id: UUID
    fecha: date
    starts_at: datetime
    ends_at: datetime
    bloque: str
    capacity: int
    is_active: bool


@dataclass(eq=False, kw_only=True)
class Turno(AggregateRoot):
    id: UUID
    user_id: UUID
    clase_id: UUID
    status: TurnoStatus
    booked_at: datetime
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    attendance_marked_at: datetime | None = None
    reminder_sent_at: datetime | None = None
    booked_by: ReservadoPor = ReservadoPor.ALUMNO

    @classmethod
    def reserve(
        cls, *, user_id: UUID, clase_id: UUID, now: datetime, by: ReservadoPor = ReservadoPor.ALUMNO
    ) -> Turno:
        turno = cls(
            id=uuid4(), user_id=user_id, clase_id=clase_id, status=TurnoStatus.CONFIRMADA, booked_at=now, booked_by=by
        )
        turno.record(TurnoReservado(turno_id=turno.id, user_id=user_id, clase_id=clase_id))
        return turno

    @property
    def is_active(self) -> bool:
        return self.status is TurnoStatus.CONFIRMADA

    def rebook(self, now: datetime, by: ReservadoPor = ReservadoPor.ALUMNO) -> None:
        """Reutiliza la fila (restricción única user_id+clase_id) tras una cancelación previa."""
        if self.status not in (TurnoStatus.CANCELADA, TurnoStatus.CANCELADA_POR_ADMIN):
            raise ConflictError("Ya tenés un turno en esta clase", code="already_booked")
        self.status = TurnoStatus.CONFIRMADA
        self.booked_at = now
        self.booked_by = by
        self.cancelled_at = None
        self.cancel_reason = None
        self.reminder_sent_at = None
        self.record(TurnoReservado(turno_id=self.id, user_id=self.user_id, clase_id=self.clase_id))

    def cancel_by_student(self, *, now: datetime, class_starts_at: datetime, min_notice: timedelta) -> None:
        if not self.is_active:
            raise ConflictError("El turno no está activo", code="booking_not_active")
        if now >= class_starts_at - min_notice:
            minutes = int(min_notice.total_seconds() // 60)
            raise BusinessRuleViolation(
                f"Sólo se puede cancelar hasta {minutes} minutos antes del inicio de la clase",
                code="cancellation_window_closed",
            )
        self._cancel(TurnoStatus.CANCELADA, now, None, CanceladoPor.ALUMNO)

    def cancel_by_gym(self, *, now: datetime, motivo: str) -> None:
        if self.is_active:
            self._cancel(TurnoStatus.CANCELADA_POR_ADMIN, now, motivo, CanceladoPor.GIMNASIO)

    def _cancel(self, status: TurnoStatus, now: datetime, motivo: str | None, por: CanceladoPor) -> None:
        self.status = status
        self.cancelled_at = now
        self.cancel_reason = motivo
        self.record(
            TurnoCancelado(turno_id=self.id, user_id=self.user_id, clase_id=self.clase_id, por=por.value, motivo=motivo)
        )

    def mark_attendance(self, *, attended: bool, now: datetime, class_date: date) -> None:
        if self.status not in (TurnoStatus.CONFIRMADA, TurnoStatus.ASISTIO, TurnoStatus.NO_ASISTIO):
            raise ConflictError("Sólo se registra asistencia de turnos confirmados", code="booking_not_active")
        if now.date() < class_date:
            raise BusinessRuleViolation("Todavía no es el día de la clase", code="class_not_started")
        self.status = TurnoStatus.ASISTIO if attended else TurnoStatus.NO_ASISTIO
        self.attendance_marked_at = now

    def mark_reminder_sent(self, now: datetime) -> None:
        self.reminder_sent_at = now


@dataclass(eq=False, kw_only=True)
class WaitlistEntry(AggregateRoot):
    id: UUID = field(default_factory=uuid4)
    user_id: UUID
    clase_id: UUID
    created_at: datetime
    notified_at: datetime | None = None

    def mark_notified(self, now: datetime) -> None:
        self.notified_at = now
