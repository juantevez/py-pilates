from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, and_, select
from sqlalchemy.orm import Session

from pilates.booking.adapters.orm import TurnoModel, WaitlistModel
from pilates.booking.application.dto import AttendanceClassDTO, AttendeeDTO, GridRow, MyBookingDTO
from pilates.booking.application.ports import BookingQueries, ClassCatalog
from pilates.booking.domain.model import ClassSnapshot, Turno, TurnoStatus, WaitlistEntry
from pilates.booking.domain.repositories import TurnoRepository, WaitlistRepository
from pilates.identity.adapters.orm import UserModel
from pilates.scheduling.adapters.orm import ClaseModel
from pilates.scheduling.domain.model import ClaseStatus
from pilates.shared.application.unit_of_work import UnitOfWork

_ACTIVE = TurnoStatus.CONFIRMADA


def _turno_to_domain(m: TurnoModel) -> Turno:
    return Turno(
        id=m.id, user_id=m.user_id, clase_id=m.clase_id, status=m.status, booked_at=m.booked_at,
        cancelled_at=m.cancelled_at, cancel_reason=m.cancel_reason, attendance_marked_at=m.attendance_marked_at,
        reminder_sent_at=m.reminder_sent_at, booked_by=m.booked_by, cancelled_with_class=m.cancelled_with_class,
    )


def _turno_to_model(t: Turno) -> TurnoModel:
    return TurnoModel(
        id=t.id, user_id=t.user_id, clase_id=t.clase_id, status=t.status, booked_at=t.booked_at,
        cancelled_at=t.cancelled_at, cancel_reason=t.cancel_reason, attendance_marked_at=t.attendance_marked_at,
        reminder_sent_at=t.reminder_sent_at, booked_by=t.booked_by, cancelled_with_class=t.cancelled_with_class,
    )


def _starts_from(moment: datetime):
    """Condición SQL: la clase empieza en o después de ``moment`` (hora local del estudio)."""
    return or_(
        ClaseModel.fecha > moment.date(),
        and_(ClaseModel.fecha == moment.date(), ClaseModel.start_time >= moment.time().replace(tzinfo=None)),
    )


def _starts_until(moment: datetime):
    return or_(
        ClaseModel.fecha < moment.date(),
        and_(ClaseModel.fecha == moment.date(), ClaseModel.start_time <= moment.time().replace(tzinfo=None)),
    )


class SqlTurnoRepository(TurnoRepository):
    def __init__(self, session: Session, uow: UnitOfWork, tz: ZoneInfo) -> None:
        self._s, self._uow, self._tz = session, uow, tz

    def _local(self, moment: datetime) -> datetime:
        return moment.astimezone(self._tz)

    def _many(self, stmt) -> list[Turno]:
        return [_turno_to_domain(m) for m in self._s.execute(stmt).scalars()]

    def add(self, turno: Turno) -> None:
        self._s.add(_turno_to_model(turno))
        self._uow.track(turno)

    def save(self, turno: Turno) -> None:
        self._s.merge(_turno_to_model(turno))
        self._uow.track(turno)

    def get(self, turno_id: UUID) -> Turno | None:
        m = self._s.execute(select(TurnoModel).where(TurnoModel.id == turno_id).with_for_update()).scalar_one_or_none()
        return _turno_to_domain(m) if m else None

    def get_by_user_and_clase(self, user_id: UUID, clase_id: UUID) -> Turno | None:
        m = self._s.execute(
            select(TurnoModel).where(TurnoModel.user_id == user_id, TurnoModel.clase_id == clase_id)
        ).scalar_one_or_none()
        return _turno_to_domain(m) if m else None

    def count_active(self, clase_id: UUID) -> int:
        return self._s.execute(
            select(func.count(TurnoModel.id)).where(TurnoModel.clase_id == clase_id, TurnoModel.status == _ACTIVE)
        ).scalar_one()

    def list_active_by_clase(self, clase_id: UUID) -> list[Turno]:
        return self._many(select(TurnoModel).where(TurnoModel.clase_id == clase_id, TurnoModel.status == _ACTIVE))

    def list_cancelled_with_class(self, clase_id: UUID) -> list[Turno]:
        return self._many(
            select(TurnoModel).where(
                TurnoModel.clase_id == clase_id,
                TurnoModel.status == TurnoStatus.CANCELADA_POR_ADMIN,
                TurnoModel.cancelled_with_class.is_(True),
            )
        )

    def list_active_by_user_on_date(self, user_id: UUID, fecha: date) -> list[Turno]:
        return self._many(
            select(TurnoModel)
            .join(ClaseModel, ClaseModel.id == TurnoModel.clase_id)
            .where(TurnoModel.user_id == user_id, TurnoModel.status == _ACTIVE, ClaseModel.fecha == fecha)
        )

    def list_active_by_user_from(self, user_id: UUID, desde: datetime) -> list[Turno]:
        return self._many(
            select(TurnoModel)
            .join(ClaseModel, ClaseModel.id == TurnoModel.clase_id)
            .where(TurnoModel.user_id == user_id, TurnoModel.status == _ACTIVE, _starts_from(self._local(desde)))
        )

    def list_pending_reminders(self, desde: datetime, hasta: datetime) -> list[Turno]:
        return self._many(
            select(TurnoModel)
            .join(ClaseModel, ClaseModel.id == TurnoModel.clase_id)
            .where(
                TurnoModel.status == _ACTIVE,
                TurnoModel.reminder_sent_at.is_(None),
                ClaseModel.status == ClaseStatus.ACTIVA,
                _starts_from(self._local(desde)),
                _starts_until(self._local(hasta)),
            )
            .with_for_update(of=TurnoModel, skip_locked=True)
        )


class SqlWaitlistRepository(WaitlistRepository):
    def __init__(self, session: Session, uow: UnitOfWork) -> None:
        self._s, self._uow = session, uow

    @staticmethod
    def _to_domain(m: WaitlistModel) -> WaitlistEntry:
        return WaitlistEntry(
            id=m.id, user_id=m.user_id, clase_id=m.clase_id, created_at=m.created_at, notified_at=m.notified_at
        )

    def add(self, entry: WaitlistEntry) -> None:
        self._s.add(
            WaitlistModel(
                id=entry.id, user_id=entry.user_id, clase_id=entry.clase_id, created_at=entry.created_at,
                notified_at=entry.notified_at,
            )
        )
        self._uow.track(entry)

    def save(self, entry: WaitlistEntry) -> None:
        m = self._s.get(WaitlistModel, entry.id)
        if m is not None:
            m.notified_at = entry.notified_at
        self._uow.track(entry)

    def delete(self, entry: WaitlistEntry) -> None:
        self._s.execute(delete(WaitlistModel).where(WaitlistModel.id == entry.id))

    def get(self, user_id: UUID, clase_id: UUID) -> WaitlistEntry | None:
        m = self._s.execute(
            select(WaitlistModel).where(WaitlistModel.user_id == user_id, WaitlistModel.clase_id == clase_id)
        ).scalar_one_or_none()
        return self._to_domain(m) if m else None

    def list_by_clase(self, clase_id: UUID) -> list[WaitlistEntry]:
        rows = self._s.execute(
            select(WaitlistModel).where(WaitlistModel.clase_id == clase_id).order_by(WaitlistModel.created_at)
        )
        return [self._to_domain(m) for m in rows.scalars()]

    def delete_by_clase(self, clase_id: UUID) -> None:
        self._s.execute(delete(WaitlistModel).where(WaitlistModel.clase_id == clase_id))


class SqlClassCatalog(ClassCatalog):
    """Adaptador anti-corrupción: traduce filas de `clases` a ClassSnapshot."""

    def __init__(self, session: Session, tz: ZoneInfo) -> None:
        self._s, self._tz = session, tz

    def get(self, clase_id: UUID, *, for_update: bool = False) -> ClassSnapshot | None:
        stmt = select(ClaseModel).where(ClaseModel.id == clase_id)
        if for_update:
            stmt = stmt.with_for_update()
        m = self._s.execute(stmt).scalar_one_or_none()
        if m is None:
            return None
        return ClassSnapshot(
            id=m.id,
            fecha=m.fecha,
            starts_at=datetime.combine(m.fecha, m.start_time, tzinfo=self._tz),
            ends_at=datetime.combine(m.fecha, m.end_time, tzinfo=self._tz),
            bloque=m.bloque.value,
            capacity=m.max_capacity,
            is_active=m.status is ClaseStatus.ACTIVA,
        )


class SqlBookingQueries(BookingQueries):
    def __init__(self, session: Session) -> None:
        self._s = session

    def grid_for_date(self, fecha: date, user_id: UUID) -> list[GridRow]:
        clases = list(
            self._s.execute(select(ClaseModel).where(ClaseModel.fecha == fecha).order_by(ClaseModel.start_time)).scalars()
        )
        ids = [c.id for c in clases]
        if not ids:
            return []
        booked = dict(
            self._s.execute(
                select(TurnoModel.clase_id, func.count(TurnoModel.id))
                .where(TurnoModel.clase_id.in_(ids), TurnoModel.status == _ACTIVE)
                .group_by(TurnoModel.clase_id)
            ).all()
        )
        waiting = dict(
            self._s.execute(
                select(WaitlistModel.clase_id, func.count(WaitlistModel.id))
                .where(WaitlistModel.clase_id.in_(ids))
                .group_by(WaitlistModel.clase_id)
            ).all()
        )
        mine = dict(
            self._s.execute(
                select(TurnoModel.clase_id, TurnoModel.id).where(
                    TurnoModel.clase_id.in_(ids), TurnoModel.user_id == user_id, TurnoModel.status == _ACTIVE
                )
            ).all()
        )
        my_waitlist = set(
            self._s.execute(
                select(WaitlistModel.clase_id).where(WaitlistModel.clase_id.in_(ids), WaitlistModel.user_id == user_id)
            ).scalars()
        )
        return [
            GridRow(
                clase_id=c.id, fecha=c.fecha, start_time=c.start_time, end_time=c.end_time, bloque=c.bloque.value,
                instructor_name=c.instructor_name, capacidad=c.max_capacity, status=c.status.value,
                reservados=booked.get(c.id, 0), lista_espera=waiting.get(c.id, 0), mi_turno_id=mine.get(c.id),
                en_lista_espera=c.id in my_waitlist,
            )
            for c in clases
        ]

    def user_bookings(self, user_id: UUID) -> list[MyBookingDTO]:
        rows = self._s.execute(
            select(TurnoModel, ClaseModel)
            .join(ClaseModel, ClaseModel.id == TurnoModel.clase_id)
            .where(TurnoModel.user_id == user_id)
            .order_by(ClaseModel.fecha.desc(), ClaseModel.start_time.desc())
            .limit(200)
        ).all()
        return [
            MyBookingDTO(
                turno_id=t.id, clase_id=c.id, fecha=c.fecha, start_time=c.start_time, end_time=c.end_time,
                instructor_name=c.instructor_name, clase_status=c.status.value, status=t.status.value,
                booked_at=t.booked_at, cancelled_at=t.cancelled_at, cancel_reason=t.cancel_reason,
            )
            for t, c in rows
        ]

    def attendance_for_date(self, fecha: date) -> list[AttendanceClassDTO]:
        clases = list(
            self._s.execute(select(ClaseModel).where(ClaseModel.fecha == fecha).order_by(ClaseModel.start_time)).scalars()
        )
        rows = self._s.execute(
            select(TurnoModel, UserModel)
            .join(UserModel, UserModel.id == TurnoModel.user_id)
            .join(ClaseModel, ClaseModel.id == TurnoModel.clase_id)
            .where(
                ClaseModel.fecha == fecha,
                TurnoModel.status.in_((TurnoStatus.CONFIRMADA, TurnoStatus.ASISTIO, TurnoStatus.NO_ASISTIO)),
            )
            .order_by(UserModel.full_name)
        ).all()
        by_class: dict[UUID, list[AttendeeDTO]] = {}
        for t, u in rows:
            by_class.setdefault(t.clase_id, []).append(
                AttendeeDTO(turno_id=t.id, user_id=u.id, full_name=u.full_name, email=u.email, status=t.status.value)
            )
        return [
            AttendanceClassDTO(
                clase_id=c.id, fecha=c.fecha, start_time=c.start_time, end_time=c.end_time, status=c.status.value,
                capacidad=c.max_capacity, alumnos=by_class.get(c.id, []),
            )
            for c in clases
        ]
