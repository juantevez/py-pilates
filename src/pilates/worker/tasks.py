import logging
from functools import lru_cache

from pilates.bootstrap import Container, build_container
from pilates.config import get_settings
from pilates.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@lru_cache
def container() -> Container:
    return build_container(get_settings())


@celery_app.task(
    name="pilates.dispatch_event", bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5
)
def dispatch_event(self, event_name: str, handler_name: str, payload: dict) -> None:
    c = container()
    event = c.registry.deserialize(event_name, payload)
    c.registry.handler(event_name, handler_name)(event)


@celery_app.task(name="pilates.open_next_day", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def open_next_day() -> dict:
    result = container().generate_daily_classes.execute(notify=True)
    logger.info("Apertura nocturna: %s", result)
    return {"fecha": result.fecha.isoformat(), "estado": result.estado, "creadas": result.clases_creadas}


@celery_app.task(name="pilates.send_reminders")
def send_reminders() -> int:
    return container().send_reminders.execute()
