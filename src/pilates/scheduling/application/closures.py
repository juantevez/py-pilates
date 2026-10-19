from datetime import date, timedelta

from pilates.shared.application.unit_of_work import UnitOfWork

_LOOKAHEAD_DAYS = 90


def closed_dates_from(uow: UnitOfWork, desde: date) -> frozenset[date]:
    """Fechas con cierre cargado desde ``desde``: el calendario de reservas las saltea."""
    hasta = desde + timedelta(days=_LOOKAHEAD_DAYS)
    return frozenset(c.fecha for c in uow.cierres.list_between(desde, hasta))
