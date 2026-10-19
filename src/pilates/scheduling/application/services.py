from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
from uuid import UUID

from pilates.booking.application.services import release_class_bookings
from pilates.scheduling.application.dto import (
    AdminClassDTO,
    CalendarDayDTO,
    CierreDTO,
    GenerationResultDTO,
    StudentCalendarDayDTO,
)
from pilates.scheduling.domain.calendar import BookingCalendar, GridTemplate, is_weekday, next_weekday
from pilates.scheduling.domain.events import ClasesReabiertas, DiaSinClases, TurnosHabilitados
from pilates.scheduling.domain.model import CierreGimnasio, Clase, ClaseStatus, TipoCierre
from pilates.shared.application.ports import Clock
from pilates.shared.application.unit_of_work import UnitOfWork
from pilates.shared.domain.errors import BusinessRuleViolation, ConflictError, NotFoundError

UowFactory = Callable[[], UnitOfWork]
MAX_CLOSURE_RANGE_DAYS = 366
MAX_CALENDAR_RANGE_DAYS = 92
_ETIQUETAS = {
    TipoCierre.FERIADO_NACIONAL: "Feriado - Sin clases",
    TipoCierre.VACACIONES_GYM: "Vacaciones del estudio - Sin clases",
    TipoCierre.ENFERMEDAD_INSTRUCTOR: "Sin clases",
    TipoCierre.MANTENIMIENTO: "Cerrado por mantenimiento",
    TipoCierre.OTRO: "Sin clases",
}
_MAX_LOOKAHEAD_DAYS = 90


def next_open_day(uow: UnitOfWork, after: date) -> date:
    """Siguiente día hábil sin cierre cargado."""
    candidate = next_weekday(after)
    for _ in range(_MAX_LOOKAHEAD_DAYS):
        if uow.cierres.get_by_date(candidate) is None:
            return candidate
        candidate = next_weekday(candidate)
    return candidate


def cierre_to_dto(c: CierreGimnasio) -> CierreDTO:
    return CierreDTO(id=c.id, fecha=c.fecha, tipo=c.tipo.value, motivo=c.motivo, creado_por=c.creado_por, creado_en=c.creado_en)


def ensure_daily_grid(uow: UnitOfWork, grid: GridTemplate, fecha: date) -> tuple[int, list[UUID]]:
    """Crea los bloques faltantes del día y reabre los cerrados por un cierre ya eliminado.

    Es idempotente: (fecha, start_time) es único. Devuelve (cantidad creada, ids reabiertos).
    """
    existing = {c.start_time: c for c in uow.clases.list_by_date(fecha)}
    created = 0
    reopened: list[UUID] = []
    for slot in grid.slots:
        clase = existing.get(slot.start)
        if clase is None:
            uow.clases.add(Clase.schedule(fecha=fecha, slot=slot, grid=grid))
            created += 1
        elif clase.status is ClaseStatus.FERIADO:
            clase.reopen()
            uow.clases.save(clase)
            reopened.append(clase.id)
    return created, reopened


class GenerateDailyClasses:
    """Job de apertura nocturna (también invocable manualmente por el admin)."""

    def __init__(
        self, uow: UowFactory, clock: Clock, grid: GridTemplate, calendar: BookingCalendar, notify_closed_day: bool
    ) -> None:
        self._uow, self._clock, self._grid, self._calendar = uow, clock, grid, calendar
        self._notify_closed_day = notify_closed_day

    def execute(self, *, fecha: date | None = None, notify: bool = False) -> GenerationResultDTO:
        now = self._clock.now()
        fecha = fecha or self._calendar.target_date(now)
        if not is_weekday(fecha):
            raise BusinessRuleViolation("Sólo hay clases de lunes a viernes", code="not_a_weekday")
        if fecha < now.date():
            raise BusinessRuleViolation("No se pueden generar clases en el pasado", code="date_in_past")
        with self._uow() as uow:
            if cierre := uow.cierres.get_by_date(fecha):
                if notify and self._notify_closed_day:
                    uow.add_event(
                        DiaSinClases(fecha=fecha, motivo=cierre.descripcion, proximo_dia_habil=next_open_day(uow, fecha))
                    )
                uow.commit()
                return GenerationResultDTO(
                    fecha=fecha, estado="cerrado", clases_creadas=0, clases_reabiertas=0, motivo=cierre.descripcion
                )
            created, reopened = ensure_daily_grid(uow, self._grid, fecha)
            if notify:
                uow.add_event(TurnosHabilitados(fecha=fecha))
            if reopened:
                uow.add_event(ClasesReabiertas(clase_ids=tuple(reopened)))
            uow.commit()
            return GenerationResultDTO(
                fecha=fecha, estado="generada", clases_creadas=created, clases_reabiertas=len(reopened)
            )


class ListClassesForDate:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    def execute(self, *, fecha: date) -> list[AdminClassDTO]:
        with self._uow() as uow:
            return uow.schedule_queries.classes_for_date(fecha)


class UpdateClase:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    def execute(
        self, *, admin_id: UUID, clase_id: UUID, max_capacity: int | None = None, instructor_name: str | None = None
    ) -> None:
        with self._uow() as uow:
            clase = uow.clases.get(clase_id, for_update=True)
            if clase is None:
                raise NotFoundError("Clase inexistente")
            if max_capacity is not None:
                clase.change_capacity(max_capacity, uow.turnos.count_occupied(clase_id))
            if instructor_name is not None:
                clase.assign_instructor(instructor_name)
            uow.clases.save(clase)
            uow.audit.record(
                admin_id,
                "class.updated",
                {"clase_id": str(clase_id), "max_capacity": max_capacity, "instructor_name": instructor_name},
            )
            uow.commit()


class CancelClase:
    """Cancela una clase puntual (ej. instructor enfermo): libera cupos y avisa a los alumnos."""

    def __init__(self, uow: UowFactory, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(self, *, admin_id: UUID, clase_id: UUID, motivo: str) -> int:
        now = self._clock.now()
        with self._uow() as uow:
            clase = uow.clases.get(clase_id, for_update=True)
            if clase is None:
                raise NotFoundError("Clase inexistente")
            if clase.starts_at(now.tzinfo) <= now:
                raise BusinessRuleViolation("La clase ya comenzó", code="class_already_started")
            clase.cancel(motivo)
            uow.clases.save(clase)
            affected = release_class_bookings(uow, clase_id=clase_id, motivo=motivo, now=now)
            uow.audit.record(
                admin_id, "class.cancelled", {"clase_id": str(clase_id), "motivo": motivo, "turnos_cancelados": affected}
            )
            uow.commit()
            return affected


class ReactivateClase:
    """Deshace la cancelación de una clase puntual. Las reservas anuladas no se restauran:
    se avisa a esos alumnos para que vuelvan a reservar (el lugar pudo haberlo tomado otro)."""

    def __init__(self, uow: UowFactory, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(self, *, admin_id: UUID, clase_id: UUID) -> None:
        now = self._clock.now()
        with self._uow() as uow:
            clase = uow.clases.get(clase_id, for_update=True)
            if clase is None:
                raise NotFoundError("Clase inexistente")
            if clase.starts_at(now.tzinfo) <= now:
                raise BusinessRuleViolation("La clase ya comenzó", code="class_already_started")
            if uow.cierres.get_by_date(clase.fecha):
                raise BusinessRuleViolation(
                    "El día tiene un cierre cargado: eliminá el cierre para reabrirlo", code="day_closed"
                )
            clase.reactivate()
            uow.clases.save(clase)
            uow.add_event(ClasesReabiertas(clase_ids=(clase.id,)))
            uow.audit.record(admin_id, "class.reactivated", {"clase_id": str(clase_id)})
            uow.commit()


class CreateCierres:
    """Carga uno o varios días de cierre (rango). Si ya había reservas, se cancelan y se avisa."""

    def __init__(self, uow: UowFactory, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(
        self, *, admin_id: UUID, desde: date, hasta: date | None, tipo: TipoCierre, motivo: str | None
    ) -> list[CierreDTO]:
        now = self._clock.now()
        hasta = hasta or desde
        if hasta < desde:
            raise BusinessRuleViolation("La fecha final es anterior a la inicial", code="invalid_range")
        if desde < now.date():
            raise BusinessRuleViolation("No se pueden cargar cierres en el pasado", code="date_in_past")
        if (hasta - desde).days > MAX_CLOSURE_RANGE_DAYS:
            raise BusinessRuleViolation("El rango no puede superar un año", code="invalid_range")

        dates = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
        dates = [d for d in dates if is_weekday(d)]
        if not dates:
            raise BusinessRuleViolation("El rango no incluye días hábiles", code="invalid_range")

        with self._uow() as uow:
            taken = [d.isoformat() for d in dates if uow.cierres.get_by_date(d)]
            if taken:
                raise ConflictError(f"Ya hay cierres cargados para: {', '.join(taken)}", code="closure_exists")
            created = []
            for fecha in dates:
                cierre = CierreGimnasio.create(fecha=fecha, tipo=tipo, motivo=motivo, creado_por=admin_id, now=now)
                uow.cierres.add(cierre)
                affected = self._close_classes(uow, fecha, cierre.descripcion, now)
                uow.audit.record(
                    admin_id,
                    "closure.created",
                    {"fecha": fecha.isoformat(), "tipo": tipo.value, "motivo": cierre.motivo, "turnos_cancelados": affected},
                )
                created.append(cierre)
            uow.commit()
            return [cierre_to_dto(c) for c in created]

    @staticmethod
    def _close_classes(uow: UnitOfWork, fecha: date, motivo: str, now: datetime) -> int:
        affected = 0
        for clase in uow.clases.list_by_date(fecha):
            clase.close_for_closure(motivo)
            uow.clases.save(clase)
            affected += release_class_bookings(uow, clase_id=clase.id, motivo=motivo, now=now)
        return affected


class UpdateCierre:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    def execute(self, *, admin_id: UUID, cierre_id: UUID, tipo: TipoCierre, motivo: str | None) -> CierreDTO:
        with self._uow() as uow:
            cierre = uow.cierres.get(cierre_id)
            if cierre is None:
                raise NotFoundError("Cierre inexistente")
            cierre.update(tipo=tipo, motivo=motivo)
            uow.cierres.save(cierre)
            uow.audit.record(admin_id, "closure.updated", {"cierre_id": str(cierre_id), "tipo": tipo.value, "motivo": motivo})
            uow.commit()
            return cierre_to_dto(cierre)


class DeleteCierre:
    """Elimina cierres (un día, o un rango como vacaciones) y reabre esos días.

    Si el día ya tenía grilla (o ya está habilitado para reservar) se reconstruye. Las reservas
    canceladas no se restauran: se avisa a esos alumnos que pueden volver a reservar.
    """

    def __init__(self, uow: UowFactory, clock: Clock, grid: GridTemplate, calendar: BookingCalendar) -> None:
        self._uow, self._clock, self._grid, self._calendar = uow, clock, grid, calendar

    def execute(self, *, admin_id: UUID, cierre_id: UUID) -> int:
        now = self._clock.now()
        with self._uow() as uow:
            cierre = uow.cierres.get(cierre_id)
            if cierre is None:
                raise NotFoundError("Cierre inexistente")
            reopened = self._delete(uow, admin_id, cierre, now)
            self._notify(uow, reopened)
            uow.commit()
            return len(reopened)

    def execute_range(self, *, admin_id: UUID, desde: date, hasta: date) -> tuple[int, int]:
        """Reabre todos los días cerrados entre ``desde`` y ``hasta``. Devuelve (cierres, clases reabiertas)."""
        if hasta < desde or (hasta - desde).days > MAX_CLOSURE_RANGE_DAYS:
            raise BusinessRuleViolation("Rango inválido", code="invalid_range")
        now = self._clock.now()
        with self._uow() as uow:
            cierres = uow.cierres.list_between(desde, hasta)
            if not cierres:
                raise NotFoundError("No hay cierres cargados en ese rango")
            reopened = [cid for c in cierres for cid in self._delete(uow, admin_id, c, now)]
            self._notify(uow, reopened)
            uow.commit()
            return len(cierres), len(reopened)

    def _delete(self, uow: UnitOfWork, admin_id: UUID, cierre: CierreGimnasio, now: datetime) -> list[UUID]:
        uow.cierres.delete(cierre)
        uow.audit.record(admin_id, "closure.deleted", {"fecha": cierre.fecha.isoformat()})
        if cierre.fecha < now.date():
            return []
        if uow.clases.list_by_date(cierre.fecha) or self._calendar.is_bookable_date(cierre.fecha, now):
            return ensure_daily_grid(uow, self._grid, cierre.fecha)[1]
        return []

    @staticmethod
    def _notify(uow: UnitOfWork, reopened: list[UUID]) -> None:
        if reopened:
            uow.add_event(ClasesReabiertas(clase_ids=tuple(reopened)))


class ListCierres:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    def execute(self, *, desde: date, hasta: date) -> list[CierreDTO]:
        with self._uow() as uow:
            return [cierre_to_dto(c) for c in uow.cierres.list_between(desde, hasta)]


class GetCalendar:
    def __init__(self, uow: UowFactory) -> None:
        self._uow = uow

    def execute(self, *, desde: date, hasta: date) -> list[CalendarDayDTO]:
        _validate_calendar_range(desde, hasta)
        with self._uow() as uow:
            return uow.schedule_queries.calendar(desde, hasta)


def _validate_calendar_range(desde: date, hasta: date) -> None:
    if hasta < desde or (hasta - desde).days > MAX_CALENDAR_RANGE_DAYS:
        raise BusinessRuleViolation(
            f"Rango inválido (máximo {MAX_CALENDAR_RANGE_DAYS} días)", code="invalid_range"
        )


class GetStudentCalendar:
    """Calendario para el alumno: días hábiles y cierres (feriados, vacaciones, etc.).

    No expone datos internos del cierre (quién lo cargó, cuándo).
    """

    def __init__(self, uow: UowFactory, clock: Clock) -> None:
        self._uow, self._clock = uow, clock

    def execute(self, *, desde: date | None = None, hasta: date | None = None) -> list[StudentCalendarDayDTO]:
        desde = desde or self._clock.now().date()
        hasta = hasta or desde + timedelta(days=30)
        _validate_calendar_range(desde, hasta)
        with self._uow() as uow:
            cierres = {c.fecha: c for c in uow.cierres.list_between(desde, hasta)}
        days = []
        for i in range((hasta - desde).days + 1):
            fecha = desde + timedelta(days=i)
            if cierre := cierres.get(fecha):
                days.append(
                    StudentCalendarDayDTO(
                        fecha=fecha,
                        hay_clases=False,
                        tipo_cierre=cierre.tipo.value,
                        motivo=cierre.motivo,
                        etiqueta=_ETIQUETAS[cierre.tipo],
                    )
                )
            else:
                days.append(StudentCalendarDayDTO(fecha=fecha, hay_clases=is_weekday(fecha)))
        return days
