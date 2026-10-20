from __future__ import annotations

from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from pilates.booking.adapters.persistence import (
    SqlBookingQueries,
    SqlClassCatalog,
    SqlTurnoRepository,
    SqlWaitlistRepository,
)
from pilates.identity.adapters.persistence import (
    SqlPasswordResetTokenRepository,
    SqlSesionRepository,
    SqlUserQueries,
    SqlUserRepository,
)
from pilates.scheduling.adapters.persistence import SqlCierreRepository, SqlClaseRepository, SqlScheduleQueries
from pilates.shared.application.unit_of_work import UnitOfWork
from pilates.shared.domain.errors import ConflictError
from pilates.shared.domain.events import DomainEvent
from pilates.shared.infrastructure.audit import SqlAuditLog
from pilates.shared.infrastructure.outbox import Outbox


class SqlAlchemyUnitOfWork(UnitOfWork):
    def __init__(self, session_factory: sessionmaker, outbox: Outbox, tz: ZoneInfo) -> None:
        super().__init__()
        self._session_factory = session_factory
        self._outbox = outbox
        self._tz = tz
        self.session: Session | None = None

    def __enter__(self) -> SqlAlchemyUnitOfWork:
        super().__enter__()
        s = self.session = self._session_factory()
        self.users = SqlUserRepository(s, self)
        self.reset_tokens = SqlPasswordResetTokenRepository(s, self)
        self.sesiones = SqlSesionRepository(s, self)
        self.user_queries = SqlUserQueries(s)
        self.clases = SqlClaseRepository(s, self)
        self.cierres = SqlCierreRepository(s, self)
        self.schedule_queries = SqlScheduleQueries(s)
        self.turnos = SqlTurnoRepository(s, self, self._tz)
        self.waitlist = SqlWaitlistRepository(s, self)
        self.class_catalog = SqlClassCatalog(s, self._tz)
        self.booking_queries = SqlBookingQueries(s)
        self.audit = SqlAuditLog(s)
        return self

    def __exit__(self, *exc) -> None:
        super().__exit__(*exc)
        if self.session is not None:
            self.session.close()
            self.session = None

    def _commit(self, events: list[DomainEvent]) -> None:
        message_ids = self._outbox.stage(self.session, events)
        try:
            self.session.commit()
        except IntegrityError as e:
            # Última línea de defensa ante carreras (restricciones únicas de la base).
            self.session.rollback()
            raise ConflictError("La operación entra en conflicto con datos existentes", code="integrity_conflict") from e
        self._outbox.notify(message_ids)

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()
