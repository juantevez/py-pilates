from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from pilates.booking.adapters.orm import TurnoModel, WaitlistModel
from pilates.booking.domain.model import TurnoStatus
from pilates.scheduling.adapters.orm import CierreModel, ClaseModel
from pilates.scheduling.application.dto import AdminClassDTO, CalendarDayDTO, CierreDTO
from pilates.scheduling.application.ports import ScheduleQueries
from pilates.scheduling.domain.calendar import is_weekday
from pilates.scheduling.domain.model import CierreGimnasio, Clase
from pilates.scheduling.domain.repositories import CierreRepository, ClaseRepository
from pilates.shared.application.unit_of_work import UnitOfWork

_BOOKED_STATUSES = (TurnoStatus.CONFIRMADA, TurnoStatus.ASISTIO, TurnoStatus.NO_ASISTIO)


def clase_to_domain(m: ClaseModel) -> Clase:
    return Clase(
        id=m.id, fecha=m.fecha, start_time=m.start_time, end_time=m.end_time, bloque=m.bloque,
        max_capacity=m.max_capacity, status=m.status, instructor_name=m.instructor_name,
        cancel_reason=m.cancel_reason,
    )


def _clase_to_model(c: Clase) -> ClaseModel:
    return ClaseModel(
        id=c.id, fecha=c.fecha, start_time=c.start_time, end_time=c.end_time, bloque=c.bloque,
        max_capacity=c.max_capacity, status=c.status, instructor_name=c.instructor_name,
        cancel_reason=c.cancel_reason,
    )


class SqlClaseRepository(ClaseRepository):
    def __init__(self, session: Session, uow: UnitOfWork) -> None:
        self._s, self._uow = session, uow

    def add(self, clase: Clase) -> None:
        self._s.add(_clase_to_model(clase))
        self._uow.track(clase)

    def save(self, clase: Clase) -> None:
        self._s.merge(_clase_to_model(clase))
        self._uow.track(clase)

    def get(self, clase_id: UUID, *, for_update: bool = False) -> Clase | None:
        stmt = select(ClaseModel).where(ClaseModel.id == clase_id)
        if for_update:
            stmt = stmt.with_for_update()
        m = self._s.execute(stmt).scalar_one_or_none()
        return clase_to_domain(m) if m else None

    def list_by_date(self, fecha: date) -> list[Clase]:
        rows = self._s.execute(select(ClaseModel).where(ClaseModel.fecha == fecha).order_by(ClaseModel.start_time))
        return [clase_to_domain(m) for m in rows.scalars()]


def _cierre_to_domain(m: CierreModel) -> CierreGimnasio:
    return CierreGimnasio(
        id=m.id, fecha=m.fecha, tipo=m.tipo, motivo=m.motivo, creado_por=m.creado_por, creado_en=m.creado_en
    )


class SqlCierreRepository(CierreRepository):
    def __init__(self, session: Session, uow: UnitOfWork) -> None:
        self._s, self._uow = session, uow

    def add(self, cierre: CierreGimnasio) -> None:
        self._s.add(
            CierreModel(
                id=cierre.id, fecha=cierre.fecha, tipo=cierre.tipo, motivo=cierre.motivo,
                creado_por=cierre.creado_por, creado_en=cierre.creado_en,
            )
        )
        self._s.flush()
        self._uow.track(cierre)

    def save(self, cierre: CierreGimnasio) -> None:
        m = self._s.get(CierreModel, cierre.id)
        m.tipo, m.motivo = cierre.tipo, cierre.motivo
        self._uow.track(cierre)

    def delete(self, cierre: CierreGimnasio) -> None:
        self._s.execute(delete(CierreModel).where(CierreModel.id == cierre.id))

    def get(self, cierre_id: UUID) -> CierreGimnasio | None:
        m = self._s.get(CierreModel, cierre_id)
        return _cierre_to_domain(m) if m else None

    def get_by_date(self, fecha: date) -> CierreGimnasio | None:
        m = self._s.execute(select(CierreModel).where(CierreModel.fecha == fecha)).scalar_one_or_none()
        return _cierre_to_domain(m) if m else None

    def list_between(self, desde: date, hasta: date) -> list[CierreGimnasio]:
        rows = self._s.execute(
            select(CierreModel).where(CierreModel.fecha.between(desde, hasta)).order_by(CierreModel.fecha)
        )
        return [_cierre_to_domain(m) for m in rows.scalars()]


class SqlScheduleQueries(ScheduleQueries):
    def __init__(self, session: Session) -> None:
        self._s = session

    def classes_for_date(self, fecha: date) -> list[AdminClassDTO]:
        booked = (
            select(func.count(TurnoModel.id))
            .where(TurnoModel.clase_id == ClaseModel.id, TurnoModel.status.in_(_BOOKED_STATUSES))
            .correlate(ClaseModel)
            .scalar_subquery()
        )
        waiting = (
            select(func.count(WaitlistModel.id))
            .where(WaitlistModel.clase_id == ClaseModel.id)
            .correlate(ClaseModel)
            .scalar_subquery()
        )
        rows = self._s.execute(
            select(ClaseModel, booked, waiting).where(ClaseModel.fecha == fecha).order_by(ClaseModel.start_time)
        ).all()
        return [
            AdminClassDTO(
                id=c.id, fecha=c.fecha, start_time=c.start_time, end_time=c.end_time, bloque=c.bloque.value,
                max_capacity=c.max_capacity, instructor_name=c.instructor_name, status=c.status.value,
                cancel_reason=c.cancel_reason, reservados=b, lista_espera=w,
            )
            for c, b, w in rows
        ]

    def calendar(self, desde: date, hasta: date) -> list[CalendarDayDTO]:
        cierres = {
            m.fecha: m
            for m in self._s.execute(select(CierreModel).where(CierreModel.fecha.between(desde, hasta))).scalars()
        }
        clases = dict(
            self._s.execute(
                select(ClaseModel.fecha, func.count(ClaseModel.id))
                .where(ClaseModel.fecha.between(desde, hasta))
                .group_by(ClaseModel.fecha)
            ).all()
        )
        reservas = dict(
            self._s.execute(
                select(ClaseModel.fecha, func.count(TurnoModel.id))
                .join(TurnoModel, TurnoModel.clase_id == ClaseModel.id)
                .where(ClaseModel.fecha.between(desde, hasta), TurnoModel.status.in_(_BOOKED_STATUSES))
                .group_by(ClaseModel.fecha)
            ).all()
        )
        days = []
        d = desde
        while d <= hasta:
            c = cierres.get(d)
            days.append(
                CalendarDayDTO(
                    fecha=d,
                    es_dia_habil=is_weekday(d) and c is None,
                    cierre=CierreDTO(
                        id=c.id, fecha=c.fecha, tipo=c.tipo.value, motivo=c.motivo, creado_por=c.creado_por,
                        creado_en=c.creado_en,
                    ) if c else None,
                    clases=clases.get(d, 0),
                    reservas=reservas.get(d, 0),
                )
            )
            d += timedelta(days=1)
        return days
