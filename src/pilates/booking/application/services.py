from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from pilates.booking.application.dto import TurnoDTO
from pilates.booking.domain.events import LugarLiberado, TurnoCancelado
from pilates.booking.domain.model import ClassSnapshot, ReservadoPor, Turno, WaitlistEntry
from pilates.booking.domain.policy import BookingPolicy
from pilates.identity.domain.events import UserBlocked
from pilates.scheduling.application.closures import closed_dates_from
from pilates.scheduling.domain.calendar import BookingCalendar
from pilates.shared.application.ports import Clock
from pilates.shared.application.unit_of_work import UnitOfWork
from pilates.shared.domain.errors import BusinessRuleViolation, ConflictError, NotFoundError, PermissionDenied

UowFactory = Callable[[], UnitOfWork]


def to_dto(turno: Turno) -> TurnoDTO:
    return TurnoDTO(
        id=turno.id,
        clase_id=turno.clase_id,
        status=turno.status.value,
        booked_at=turno.booked_at,
        cancelled_at=turno.cancelled_at,
        booked_by=turno.booked_by.value,
    )


def _get_class(uow: UnitOfWork, clase_id: UUID, *, for_update: bool = False) -> ClassSnapshot:
    clase = uow.class_catalog.get(clase_id, for_update=for_update)
    if clase is None:
        raise NotFoundError("Clase inexistente")
    return clase


def release_class_bookings(uow: UnitOfWork, *, clase_id: UUID, motivo: str, now) -> int:
    """API pública del módulo de reservas para el contexto de agenda.

    Cancela (dentro de la misma transacción) todos los turnos activos de una clase que el
    gimnasio canceló o cerró, y vacía su lista de espera. Cada turno emite TurnoCancelado,
    que dispara el aviso al alumno.
    """
    turnos = uow.turnos.list_active_by_clase(clase_id)
    for turno in turnos:
        turno.cancel_with_class(now=now, motivo=motivo)
        uow.turnos.save(turno)
    uow.waitlist.delete_by_clase(clase_id)
    return len(turnos)


class BookClass:
    def __init__(self, uow: UowFactory, clock: Clock, calendar: BookingCalendar, policy: BookingPolicy) -> None:
        self._uow, self._clock, self._calendar, self._policy = uow, clock, calendar, policy

    def execute(self, *, user_id: UUID, clase_id: UUID) -> TurnoDTO:
        now = self._clock.now()
        with self._uow() as uow:
            # Bloqueos en orden fijo (usuario -> clase) para serializar reservas concurrentes:
            # el del usuario protege los límites diarios, el de la clase protege el cupo.
            user = uow.users.get(user_id, for_update=True)
            if user is None:
                raise NotFoundError("Usuario inexistente")
            user.ensure_active()
            clase = _get_class(uow, clase_id, for_update=True)

            existing = uow.turnos.get_by_user_and_clase(user_id, clase_id)
            if existing is not None and existing.is_active:
                raise ConflictError("Ya tenés un turno en esta clase", code="already_booked")

            same_day = [
                _get_class(uow, t.clase_id) for t in uow.turnos.list_occupied_by_user_on_date(user_id, clase.fecha)
            ]
            self._policy.ensure_can_book(
                now=now,
                clase=clase,
                within_booking_window=self._calendar.is_bookable_date(clase.fecha, now, closed_dates_from(uow, now.date())),
                active_bookings=uow.turnos.count_occupied(clase_id),
                user_bookings_same_day=same_day,
            )

            if existing is not None:
                existing.rebook(now)
                uow.turnos.save(existing)
                turno = existing
            else:
                turno = Turno.reserve(user_id=user_id, clase_id=clase_id, now=now)
                uow.turnos.add(turno)

            if entry := uow.waitlist.get(user_id, clase_id):
                uow.waitlist.delete(entry)
            uow.commit()
            return to_dto(turno)


class AdminBookClass:
    """El gimnasio reserva en nombre de un alumno: sólo se valida el cupo y que la clase no haya terminado."""

    def __init__(self, uow: UowFactory, clock: Clock, policy: BookingPolicy) -> None:
        self._uow, self._clock, self._policy = uow, clock, policy

    def execute(self, *, admin_id: UUID, user_id: UUID, clase_id: UUID) -> TurnoDTO:
        now = self._clock.now()
        with self._uow() as uow:
            # Mismo orden de bloqueos que BookClass (usuario -> clase) para no generar deadlocks.
            user = uow.users.get(user_id, for_update=True)
            if user is None:
                raise NotFoundError("Usuario inexistente")
            user.ensure_active()
            clase = _get_class(uow, clase_id, for_update=True)

            existing = uow.turnos.get_by_user_and_clase(user_id, clase_id)
            if existing is not None and existing.is_active:
                raise ConflictError("El alumno ya tiene un turno en esta clase", code="already_booked")
            self._policy.ensure_admin_can_book(
                now=now, clase=clase, active_bookings=uow.turnos.count_occupied(clase_id)
            )

            if existing is not None:
                existing.rebook(now, by=ReservadoPor.GIMNASIO)
                uow.turnos.save(existing)
                turno = existing
            else:
                turno = Turno.reserve(user_id=user_id, clase_id=clase_id, now=now, by=ReservadoPor.GIMNASIO)
                uow.turnos.add(turno)

            if entry := uow.waitlist.get(user_id, clase_id):
                uow.waitlist.delete(entry)
            uow.audit.record(admin_id, "booking.created_by_admin", {"turno_id": str(turno.id), "user_id": str(user_id)})
            uow.commit()
            return to_dto(turno)


class CancelBooking:
    def __init__(self, uow: UowFactory, clock: Clock, policy: BookingPolicy) -> None:
        self._uow, self._clock, self._policy = uow, clock, policy

    def execute(self, *, user_id: UUID, turno_id: UUID) -> TurnoDTO:
        now = self._clock.now()
        with self._uow() as uow:
            turno = uow.turnos.get(turno_id)
            if turno is None:
                raise NotFoundError("Turno inexistente")
            if turno.user_id != user_id:
                raise PermissionDenied("El turno no te pertenece")
            clase = _get_class(uow, turno.clase_id, for_update=True)
            turno.cancel_by_student(now=now, class_starts_at=clase.starts_at, min_notice=self._policy.cancel_min_notice)
            uow.turnos.save(turno)
            uow.commit()
            return to_dto(turno)


class JoinWaitlist:
    def __init__(self, uow: UowFactory, clock: Clock, calendar: BookingCalendar, policy: BookingPolicy) -> None:
        self._uow, self._clock, self._calendar, self._policy = uow, clock, calendar, policy

    def execute(self, *, user_id: UUID, clase_id: UUID) -> None:
        now = self._clock.now()
        with self._uow() as uow:
            user = uow.users.get(user_id)
            if user is None:
                raise NotFoundError("Usuario inexistente")
            user.ensure_active()
            clase = _get_class(uow, clase_id)
            existing = uow.turnos.get_by_user_and_clase(user_id, clase_id)
            if existing is not None and existing.is_active:
                raise ConflictError("Ya tenés un turno en esta clase", code="already_booked")
            if uow.waitlist.get(user_id, clase_id):
                raise ConflictError("Ya estás en la lista de espera", code="already_in_waitlist")
            self._policy.ensure_can_join_waitlist(
                now=now,
                clase=clase,
                within_booking_window=self._calendar.is_bookable_date(clase.fecha, now, closed_dates_from(uow, now.date())),
                active_bookings=uow.turnos.count_occupied(clase_id),
            )
            uow.waitlist.add(WaitlistEntry(user_id=user_id, clase_id=clase_id, created_at=now))
            uow.commit()


class PromoteFromWaitlist:
    """Handler de TurnoCancelado: el lugar liberado pasa al primero de la lista de espera.

    Se le reserva automáticamente (respetando sus límites diarios; si no puede, se pasa al
    siguiente) y se le avisa: si ya no lo quiere, lo cancela como cualquier turno. A menos de
    ``cancel_min_notice`` de la clase ya no podría cancelarlo, así que no se asigna: se emite
    LugarLiberado y se avisa a toda la lista (reserva el primero que entra).
    """

    _SKIP = ("daily_limit_reached", "block_limit_reached")

    def __init__(self, uow: UowFactory, clock: Clock, policy: BookingPolicy) -> None:
        self._uow, self._clock, self._policy = uow, clock, policy

    def __call__(self, event: TurnoCancelado) -> None:
        now = self._clock.now()
        with self._uow() as uow:
            clase = uow.class_catalog.get(event.clase_id)
            entries = uow.waitlist.list_by_clase(event.clase_id)
            if clase is None or not entries or not clase.is_active or now >= clase.starts_at:
                return
            if now >= clase.starts_at - self._policy.cancel_min_notice:
                if uow.turnos.count_occupied(clase.id) < clase.capacity:
                    uow.add_event(LugarLiberado(clase_id=clase.id))
                    uow.commit()
                return
        for entry in entries:  # orden de llegada
            if not self._try_promote(entry.user_id, event.clase_id, now):
                break

    def _try_promote(self, user_id: UUID, clase_id: UUID, now) -> bool:
        """Una transacción por candidato. Devuelve False cuando no tiene sentido seguir (sin lugar)."""
        with self._uow() as uow:
            # Mismo orden de bloqueos que BookClass (usuario -> clase).
            user = uow.users.get(user_id, for_update=True)
            clase = uow.class_catalog.get(clase_id, for_update=True)
            if clase is None:
                return False
            occupied = uow.turnos.count_occupied(clase_id)
            if occupied >= clase.capacity:
                return False
            entry = uow.waitlist.get(user_id, clase_id)
            if entry is None or user is None or user.is_blocked:
                return True  # se bajó de la lista o no puede reservar: siguiente
            existing = uow.turnos.get_by_user_and_clase(user_id, clase_id)
            if existing is not None and existing.is_active:
                uow.waitlist.delete(entry)
                uow.commit()
                return True
            same_day = [
                _get_class(uow, t.clase_id) for t in uow.turnos.list_occupied_by_user_on_date(user_id, clase.fecha)
            ]
            try:
                self._policy.ensure_can_book(
                    now=now, clase=clase, within_booking_window=True, active_bookings=occupied,
                    user_bookings_same_day=same_day,
                )
            except BusinessRuleViolation as e:
                return e.code in self._SKIP  # límites del alumno: siguiente; clase cerrada/empezada: cortar
            except ConflictError:
                return False
            if existing is not None:
                existing.rebook(now, from_waitlist=True)
                uow.turnos.save(existing)
            else:
                uow.turnos.add(Turno.reserve(user_id=user_id, clase_id=clase_id, now=now, from_waitlist=True))
            uow.waitlist.delete(entry)
            uow.commit()
            return True  # por si se liberó más de un lugar


class LeaveWaitlist:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    def execute(self, *, user_id: UUID, clase_id: UUID) -> None:
        with self._uow() as uow:
            entry = uow.waitlist.get(user_id, clase_id)
            if entry is None:
                raise NotFoundError("No estás en la lista de espera de esta clase")
            uow.waitlist.delete(entry)
            uow.commit()


class AdminCancelBooking:
    """El gimnasio cancela un turno puntual: libera el cupo y avisa al alumno y a la lista de espera."""

    def __init__(self, uow: UowFactory, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(self, *, admin_id: UUID, turno_id: UUID, motivo: str | None) -> TurnoDTO:
        now = self._clock.now()
        with self._uow() as uow:
            turno = uow.turnos.get(turno_id)
            if turno is None:
                raise NotFoundError("Turno inexistente")
            clase = _get_class(uow, turno.clase_id, for_update=True)
            turno.cancel_by_admin(now=now, class_ends_at=clase.ends_at, motivo=motivo)
            uow.turnos.save(turno)
            uow.audit.record(admin_id, "booking.cancelled_by_admin", {"turno_id": str(turno_id), "motivo": motivo})
            uow.commit()
            return to_dto(turno)


class MarkAttendance:
    def __init__(self, uow: UowFactory, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(self, *, admin_id: UUID, turno_id: UUID, asistio: bool) -> TurnoDTO:
        now = self._clock.now()
        with self._uow() as uow:
            turno = uow.turnos.get(turno_id)
            if turno is None:
                raise NotFoundError("Turno inexistente")
            clase = _get_class(uow, turno.clase_id)
            turno.mark_attendance(attended=asistio, now=now, class_date=clase.fecha)
            uow.turnos.save(turno)
            uow.audit.record(admin_id, "booking.attendance", {"turno_id": str(turno_id), "asistio": asistio})
            uow.commit()
            return to_dto(turno)


class CancelUpcomingBookingsOfBlockedUser:
    """Handler de UserBlocked: libera los cupos futuros del alumno bloqueado."""

    def __init__(self, uow: UowFactory, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def __call__(self, event: UserBlocked) -> None:
        now = self._clock.now()
        with self._uow() as uow:
            for turno in uow.turnos.list_active_by_user_from(event.user_id, now):
                turno.cancel_by_gym(now=now, motivo="Cuenta suspendida")
                uow.turnos.save(turno)
            uow.commit()
