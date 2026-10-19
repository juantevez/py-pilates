from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from uuid import UUID

from pilates.booking.application.dto import (
    AttendanceClassDTO,
    ClassAvailabilityDTO,
    DayAvailabilityDTO,
    GridRow,
    MyBookingsDTO,
)
from pilates.booking.domain.model import ClassSnapshot, TurnoStatus
from pilates.booking.domain.policy import BookingPolicy
from pilates.scheduling.application.services import next_open_day
from pilates.scheduling.domain.calendar import BookingCalendar
from pilates.scheduling.domain.model import ClaseStatus
from pilates.shared.application.ports import Clock
from pilates.shared.application.unit_of_work import UnitOfWork

UowFactory = Callable[[], UnitOfWork]


class GetAvailability:
    """Grilla visible para el alumno: sólo el siguiente día hábil (y hoy, si está habilitado)."""

    def __init__(self, uow: UowFactory, clock: Clock, calendar: BookingCalendar, policy: BookingPolicy) -> None:
        self._uow, self._clock, self._calendar, self._policy = uow, clock, calendar, policy

    def execute(self, *, user_id: UUID) -> list[DayAvailabilityDTO]:
        now = self._clock.now()
        with self._uow() as uow:
            return [self._day(uow, fecha, user_id, now) for fecha in self._calendar.bookable_dates(now)]

    def _day(self, uow: UnitOfWork, fecha: date, user_id: UUID, now: datetime) -> DayAvailabilityDTO:
        if cierre := uow.cierres.get_by_date(fecha):
            return DayAvailabilityDTO(
                fecha=fecha,
                estado="cerrado",
                motivo_cierre=cierre.descripcion,
                proximo_dia_habil=next_open_day(uow, fecha),
            )
        apertura = None if fecha == now.date() else self._calendar.opening_at(fecha, now)
        if apertura is not None and now < apertura:
            return DayAvailabilityDTO(fecha=fecha, estado="pendiente_apertura", apertura=apertura)

        rows = uow.booking_queries.grid_for_date(fecha, user_id)
        if not rows:
            return DayAvailabilityDTO(fecha=fecha, estado="sin_clases", apertura=apertura)
        within_window = self._calendar.is_bookable_date(fecha, now)
        return DayAvailabilityDTO(
            fecha=fecha,
            estado="abierta",
            apertura=apertura,
            clases=[self._class(row, now, within_window) for row in rows],
        )

    def _class(self, row: GridRow, now: datetime, within_window: bool) -> ClassAvailabilityDTO:
        starts_at = datetime.combine(row.fecha, row.start_time, tzinfo=now.tzinfo)
        snapshot = ClassSnapshot(
            id=row.clase_id,
            fecha=row.fecha,
            starts_at=starts_at,
            ends_at=datetime.combine(row.fecha, row.end_time, tzinfo=now.tzinfo),
            bloque=row.bloque,
            capacity=row.capacidad,
            is_active=row.status == ClaseStatus.ACTIVA.value,
        )
        reservable = self._policy.can_book(now=now, clase=snapshot, within_booking_window=within_window)
        disponibles = max(row.capacidad - row.reservados, 0)
        completa = disponibles == 0
        tiene_turno = row.mi_turno_id is not None
        return ClassAvailabilityDTO(
            clase_id=row.clase_id,
            fecha=row.fecha,
            start_time=row.start_time,
            end_time=row.end_time,
            bloque=row.bloque,
            instructor_name=row.instructor_name,
            capacidad=row.capacidad,
            reservados=row.reservados,
            disponibles=disponibles,
            completa=completa,
            status=row.status,
            mi_turno_id=row.mi_turno_id,
            en_lista_espera=row.en_lista_espera,
            puede_reservar=reservable and not completa and not tiene_turno,
            puede_cancelar=tiene_turno and self._policy.can_cancel(now=now, class_starts_at=starts_at),
            puede_anotarse_lista_espera=reservable and completa and not tiene_turno and not row.en_lista_espera,
        )


class GetMyBookings:
    def __init__(self, uow: UowFactory, clock: Clock, policy: BookingPolicy) -> None:
        self._uow, self._clock, self._policy = uow, clock, policy

    def execute(self, *, user_id: UUID) -> MyBookingsDTO:
        now = self._clock.now()
        with self._uow() as uow:
            rows = uow.booking_queries.user_bookings(user_id)
        proximas, pasadas = [], []
        for row in rows:
            starts_at = datetime.combine(row.fecha, row.start_time, tzinfo=now.tzinfo)
            if starts_at >= now:
                cancelable = row.status == TurnoStatus.CONFIRMADA.value and self._policy.can_cancel(
                    now=now, class_starts_at=starts_at
                )
                proximas.append(replace(row, puede_cancelar=cancelable))
            else:
                pasadas.append(row)
        proximas.sort(key=lambda r: (r.fecha, r.start_time))
        pasadas.sort(key=lambda r: (r.fecha, r.start_time), reverse=True)
        return MyBookingsDTO(proximas=proximas, pasadas=pasadas)


class GetAttendanceSheet:
    def __init__(self, uow: UowFactory, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(self, *, fecha: date | None = None) -> list[AttendanceClassDTO]:
        fecha = fecha or self._clock.now().date()
        with self._uow() as uow:
            return uow.booking_queries.attendance_for_date(fecha)
