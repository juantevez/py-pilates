"""Outbox transaccional de eventos de dominio.

Los eventos se guardan en la tabla ``outbox`` en la misma transacción que los cambios que los
originan, una fila por par (evento, handler). Tras el commit se avisa al despachador para
procesarlos enseguida; si ese aviso se pierde (Redis caído, worker reiniciado), el relay
periódico los vuelve a enviar.

Entrega al menos una vez: el despacho es idempotente por fila (``processed_at``), pero un
handler puede repetirse si el proceso muere entre que termina y se marca la fila.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from pilates.shared.domain.events import DomainEvent
from pilates.shared.infrastructure.event_bus import EventRegistry
from pilates.shared.infrastructure.orm import OutboxModel

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 10  # después de esto la fila queda para revisión manual
RELAY_GRACE = timedelta(minutes=2)  # margen para que el aviso inmediato llegue primero
RELAY_REQUEUE_AFTER = timedelta(minutes=5)  # no reenviar mientras Celery sigue reintentando
RELAY_BATCH = 500
RETENTION = timedelta(days=7)


class OutboxNotifier(ABC):
    """Avisa que hay filas del outbox listas para procesar."""

    @abstractmethod
    def notify(self, message_ids: list[UUID]) -> None: ...


class Outbox:
    def __init__(self, registry: EventRegistry, notifier: OutboxNotifier) -> None:
        self._registry = registry
        self.notifier = notifier

    def stage(self, session: Session, events: list[DomainEvent]) -> list[UUID]:
        """Agrega los eventos a la sesión (antes del commit). Devuelve los ids de las filas."""
        now = datetime.now(UTC)
        ids = []
        for event in events:
            payload = self._registry.serialize(event)
            for handler_name in self._registry.handler_names(event):
                row = OutboxModel(
                    id=uuid4(), event_name=event.name(), handler_name=handler_name, payload=payload,
                    created_at=now, attempts=0,
                )
                session.add(row)
                ids.append(row.id)
        return ids

    def notify(self, message_ids: list[UUID]) -> None:
        """Después del commit: un fallo acá no se propaga, las filas ya quedaron guardadas."""
        if not message_ids:
            return
        try:
            self.notifier.notify(message_ids)
        except Exception:
            logger.warning(
                "No se pudo avisar %d evento(s) del outbox; los reenvía el relay", len(message_ids), exc_info=True
            )


class OutboxDispatcher:
    def __init__(self, session_factory: sessionmaker, registry: EventRegistry) -> None:
        self._session_factory, self._registry = session_factory, registry

    def dispatch(self, message_id: UUID) -> bool:
        """Ejecuta el handler de la fila si sigue pendiente.

        Devuelve False si ya se procesó o si la está procesando otro proceso (lock tomado).
        Si el handler falla, suma un intento, guarda el error y relanza la excepción.
        """
        with self._session_factory() as s:
            row = s.execute(
                select(OutboxModel)
                .where(OutboxModel.id == message_id, OutboxModel.processed_at.is_(None))
                .with_for_update(skip_locked=True)
            ).scalar_one_or_none()
            if row is None:
                return False
            try:
                event = self._registry.deserialize(row.event_name, row.payload)
                self._registry.handler(row.event_name, row.handler_name)(event)
            except Exception as e:
                s.rollback()
                self._record_failure(message_id, e)
                raise
            row.processed_at = datetime.now(UTC)
            s.commit()
            return True

    def _record_failure(self, message_id: UUID, error: Exception) -> None:
        with self._session_factory() as s:
            s.execute(
                update(OutboxModel)
                .where(OutboxModel.id == message_id)
                .values(attempts=OutboxModel.attempts + 1, last_error=f"{type(error).__name__}: {error}"[:2000])
            )
            s.commit()


class SyncOutboxNotifier(OutboxNotifier):
    """Procesa los eventos en el mismo proceso, al toque (desarrollo y tests)."""

    def __init__(self, dispatcher: OutboxDispatcher) -> None:
        self._dispatcher = dispatcher

    def notify(self, message_ids: list[UUID]) -> None:
        for message_id in message_ids:
            try:
                self._dispatcher.dispatch(message_id)
            except Exception:
                logger.exception("Falló el evento %s del outbox; queda pendiente para el relay", message_id)


class CeleryOutboxNotifier(OutboxNotifier):
    def __init__(self, send_task: Callable[..., Any]) -> None:
        self._send_task = send_task

    def notify(self, message_ids: list[UUID]) -> None:
        for message_id in message_ids:
            self._send_task("pilates.dispatch_outbox", args=[str(message_id)])


class OutboxRelay:
    """Tarea periódica: reenvía las filas pendientes cuyo aviso se perdió y purga las procesadas."""

    def __init__(self, session_factory: sessionmaker, outbox: Outbox) -> None:
        self._session_factory, self._outbox = session_factory, outbox

    def execute(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        with self._session_factory() as s:
            rows = s.execute(
                select(OutboxModel)
                .where(
                    OutboxModel.processed_at.is_(None),
                    OutboxModel.attempts < MAX_ATTEMPTS,
                    OutboxModel.created_at <= now - RELAY_GRACE,
                    or_(OutboxModel.last_relayed_at.is_(None), OutboxModel.last_relayed_at <= now - RELAY_REQUEUE_AFTER),
                )
                .order_by(OutboxModel.created_at)
                .limit(RELAY_BATCH)
                .with_for_update(skip_locked=True)
            ).scalars().all()
            ids = [r.id for r in rows]
            for r in rows:
                r.last_relayed_at = now
            s.execute(delete(OutboxModel).where(OutboxModel.processed_at < now - RETENTION))
            s.commit()
            stuck = s.execute(
                select(func.count(OutboxModel.id)).where(
                    OutboxModel.processed_at.is_(None), OutboxModel.attempts >= MAX_ATTEMPTS
                )
            ).scalar_one()
        if stuck:
            logger.error("Hay %d evento(s) del outbox que agotaron los reintentos; revisá last_error", stuck)
        self._outbox.notify(ids)
        return len(ids)
