from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from pilates.booking.domain.events import TurnoCancelado, TurnoReservado
from pilates.booking.domain.model import ClassSnapshot, Turno, TurnoStatus
from pilates.booking.domain.policy import BookingPolicy
from pilates.shared.domain.errors import BusinessRuleViolation, ConflictError

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
NOW = datetime(2026, 10, 12, 21, 0, tzinfo=TZ)
POLICY = BookingPolicy(max_per_day=2, max_per_block=1, min_advance=timedelta(0), cancel_min_notice=timedelta(hours=1))


def snapshot(hour=9, bloque="manana", capacity=4, active=True) -> ClassSnapshot:
    fecha = date(2026, 10, 13)
    return ClassSnapshot(
        id=uuid4(), fecha=fecha, starts_at=datetime(2026, 10, 13, hour, tzinfo=TZ),
        ends_at=datetime(2026, 10, 13, hour + 1, tzinfo=TZ), bloque=bloque,
        capacity=capacity, is_active=active,
    )


def book(**kw):
    defaults = dict(now=NOW, clase=snapshot(), within_booking_window=True, active_bookings=0, user_bookings_same_day=[])
    POLICY.ensure_can_book(**(defaults | kw))


def test_can_book_happy_path():
    book()


def test_full_class_is_rejected():
    with pytest.raises(ConflictError) as e:
        book(active_bookings=4)
    assert e.value.code == "class_full"


def test_outside_window_is_rejected():
    with pytest.raises(BusinessRuleViolation) as e:
        book(within_booking_window=False)
    assert e.value.code == "outside_booking_window"


def test_cancelled_class_is_rejected():
    with pytest.raises(BusinessRuleViolation) as e:
        book(clase=snapshot(active=False))
    assert e.value.code == "class_not_available"


def test_one_class_per_block():
    with pytest.raises(BusinessRuleViolation) as e:
        book(clase=snapshot(hour=10), user_bookings_same_day=[snapshot(hour=8)])
    assert e.value.code == "block_limit_reached"


def test_max_two_per_day():
    with pytest.raises(BusinessRuleViolation) as e:
        book(clase=snapshot(hour=17, bloque="tarde"),
             user_bookings_same_day=[snapshot(hour=8), snapshot(hour=15, bloque="tarde")])
    assert e.value.code == "daily_limit_reached"


def test_morning_and_afternoon_allowed():
    book(clase=snapshot(hour=16, bloque="tarde"), user_bookings_same_day=[snapshot(hour=8)])


def test_turno_cancel_window():
    turno = Turno.reserve(user_id=uuid4(), clase_id=uuid4(), now=NOW)
    starts = datetime(2026, 10, 13, 9, tzinfo=TZ)
    with pytest.raises(BusinessRuleViolation) as e:
        turno.cancel_by_student(now=starts - timedelta(minutes=59), class_starts_at=starts, min_notice=timedelta(hours=1))
    assert e.value.code == "cancellation_window_closed"
    turno.cancel_by_student(now=starts - timedelta(minutes=61), class_starts_at=starts, min_notice=timedelta(hours=1))
    assert turno.status is TurnoStatus.CANCELADA
    events = turno.pull_events()
    assert [type(e) for e in events] == [TurnoReservado, TurnoCancelado]
    assert events[1].por == "alumno"


def test_rebook_after_cancel_and_attendance():
    turno = Turno.reserve(user_id=uuid4(), clase_id=uuid4(), now=NOW)
    turno.cancel_by_gym(now=NOW, motivo="Instructor enfermo")
    assert turno.status is TurnoStatus.CANCELADA_POR_ADMIN
    turno.rebook(NOW)
    assert turno.is_active
    with pytest.raises(BusinessRuleViolation):
        turno.mark_attendance(attended=True, now=NOW, class_date=date(2026, 10, 13))
    turno.mark_attendance(attended=False, now=NOW + timedelta(days=1), class_date=date(2026, 10, 13))
    assert turno.status is TurnoStatus.NO_ASISTIO


def test_clase_reactivation_rules():
    from datetime import time as t

    from pilates.scheduling.domain.calendar import GridTemplate
    from pilates.scheduling.domain.model import Clase, ClaseStatus

    grid = GridTemplate.from_times([t(8)], [], timedelta(hours=1), 4)
    clase = Clase.schedule(fecha=date(2026, 10, 13), slot=grid.slots[0], grid=grid)
    with pytest.raises(ConflictError):
        clase.reactivate()  # ya activa
    clase.cancel("gripe")
    clase.reactivate()
    assert clase.status is ClaseStatus.ACTIVA and clase.cancel_reason is None
    clase.close_for_closure("feriado")
    with pytest.raises(BusinessRuleViolation) as e:
        clase.reactivate()
    assert e.value.code == "class_closed_by_closure"


def test_admin_booking_only_checks_capacity_and_class_not_finished():
    clase = snapshot(hour=9)
    # Fuera de ventana y con la clase en curso: el gimnasio igual puede reservar.
    POLICY.ensure_admin_can_book(now=datetime(2026, 10, 13, 9, 30, tzinfo=TZ), clase=clase, active_bookings=3)
    with pytest.raises(ConflictError) as e:
        POLICY.ensure_admin_can_book(now=NOW, clase=clase, active_bookings=4)
    assert e.value.code == "class_full"
    with pytest.raises(BusinessRuleViolation) as e:
        POLICY.ensure_admin_can_book(now=datetime(2026, 10, 13, 10, 0, tzinfo=TZ), clase=clase, active_bookings=0)
    assert e.value.code == "class_finished"
