"""Bus de eventos de dominio.

Cada evento puede tener varios handlers, identificados por nombre. Con Celery se encola
una tarea por (evento, handler), de modo que un reintento no repite otros handlers.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter

from pilates.shared.application.ports import EventBus
from pilates.shared.domain.events import DomainEvent

logger = logging.getLogger(__name__)
Handler = Callable[[Any], None]


class EventRegistry:
    def __init__(self) -> None:
        self._types: dict[str, type[DomainEvent]] = {}
        self._handlers: dict[str, dict[str, Handler]] = {}

    def subscribe(self, event_type: type[DomainEvent], handler_name: str, handler: Handler) -> None:
        self._types[event_type.name()] = event_type
        self._handlers.setdefault(event_type.name(), {})[handler_name] = handler

    def handler_names(self, event: DomainEvent) -> list[str]:
        return list(self._handlers.get(event.name(), {}))

    def handler(self, event_name: str, handler_name: str) -> Handler:
        return self._handlers[event_name][handler_name]

    def serialize(self, event: DomainEvent) -> dict:
        return TypeAdapter(type(event)).dump_python(event, mode="json")

    def deserialize(self, event_name: str, payload: dict) -> DomainEvent:
        return TypeAdapter(self._types[event_name]).validate_python(payload)


class SyncEventBus(EventBus):
    """Ejecuta los handlers en el mismo proceso (útil en desarrollo y tests)."""

    def __init__(self, registry: EventRegistry) -> None:
        self._registry = registry

    def publish(self, events: list[DomainEvent]) -> None:
        for event in events:
            for name in self._registry.handler_names(event):
                try:
                    self._registry.handler(event.name(), name)(event)
                except Exception:
                    logger.exception("Handler %s falló para %s", name, event.name())


class CeleryEventBus(EventBus):
    def __init__(self, registry: EventRegistry, send_task: Callable[..., Any]) -> None:
        self._registry = registry
        self._send_task = send_task

    def publish(self, events: list[DomainEvent]) -> None:
        for event in events:
            payload = self._registry.serialize(event)
            for name in self._registry.handler_names(event):
                self._send_task("pilates.dispatch_event", args=[event.name(), name, payload])
