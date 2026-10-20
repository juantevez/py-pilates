"""Registro de handlers de eventos de dominio.

Cada evento puede tener varios handlers, identificados por nombre. El outbox guarda una fila
por (evento, handler), de modo que un reintento no repite otros handlers (ver ``outbox.py``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter

from pilates.shared.domain.events import DomainEvent

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
