from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from pilates.scheduling.domain.calendar import BookingCalendar, GridTemplate, TimeBlock, next_weekday

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
CAL = BookingCalendar(opening_time=time(20, 0))


def at(y, m, d, hh, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def test_next_weekday_skips_weekend():
    friday = date(2026, 10, 16)
    assert next_weekday(friday) == date(2026, 10, 19)


def test_target_is_next_business_day():
    assert CAL.target_date(at(2026, 10, 12, 10)) == date(2026, 10, 13)  # lunes -> martes
    assert CAL.target_date(at(2026, 10, 16, 21)) == date(2026, 10, 19)  # viernes -> lunes
    assert CAL.target_date(at(2026, 10, 17, 9)) == date(2026, 10, 19)  # sábado -> lunes


@pytest.mark.parametrize(
    "now,fecha,expected",
    [
        (at(2026, 10, 12, 19, 59), date(2026, 10, 13), False),  # antes de la apertura
        (at(2026, 10, 12, 20, 0), date(2026, 10, 13), True),  # apertura
        (at(2026, 10, 12, 21), date(2026, 10, 14), False),  # pasado mañana: fuera de horizonte
        (at(2026, 10, 12, 7), date(2026, 10, 12), False),  # mismo día no permitido
        (at(2026, 10, 17, 9), date(2026, 10, 19), True),  # sábado: ya abierto el lunes
    ],
)
def test_is_bookable_date(now, fecha, expected):
    assert CAL.is_bookable_date(fecha, now) is expected


def test_same_day_booking_when_enabled():
    cal = BookingCalendar(opening_time=time(20, 0), allow_same_day=True)
    assert cal.is_bookable_date(date(2026, 10, 12), at(2026, 10, 12, 7))
    assert cal.bookable_dates(at(2026, 10, 12, 7)) == [date(2026, 10, 12), date(2026, 10, 13)]


def test_grid_template_blocks():
    grid = GridTemplate.from_times(
        [time(8), time(9), time(10), time(11)], [time(15), time(16), time(17)], timedelta(hours=1), 4
    )
    assert len(grid.slots) == 7
    assert [s.block for s in grid.slots].count(TimeBlock.TARDE) == 3
    assert grid.end_of(time(17)) == time(18)


def test_closed_days_are_skipped():
    tuesday, wednesday = date(2026, 10, 13), date(2026, 10, 14)
    closed = {tuesday}
    assert CAL.target_date(at(2026, 10, 12, 10), closed) == wednesday
    assert CAL.opening_at(wednesday, at(2026, 10, 12, 10), closed) == at(2026, 10, 12, 20)
    assert not CAL.is_bookable_date(wednesday, at(2026, 10, 12, 19, 59), closed)
    assert CAL.is_bookable_date(wednesday, at(2026, 10, 12, 20), closed)
    assert CAL.is_bookable_date(wednesday, at(2026, 10, 13, 9), closed)  # durante el día cerrado
    assert CAL.bookable_dates(at(2026, 10, 12, 21), closed) == [tuesday, wednesday]
    # Viernes cerrado: el jueves a las 20:00 se abren los del lunes.
    assert CAL.target_date(at(2026, 10, 15, 21), {date(2026, 10, 16)}) == date(2026, 10, 19)
    assert CAL.opening_at(date(2026, 10, 19), at(2026, 10, 15, 21), {date(2026, 10, 16)}) == at(2026, 10, 15, 20)
