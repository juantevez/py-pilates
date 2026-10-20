from celery import Celery
from celery.schedules import crontab

from pilates.config import get_settings

settings = get_settings()

celery_app = Celery("pilates", broker=settings.redis_url, backend=None, include=["pilates.worker.tasks"])
celery_app.conf.update(
    timezone=settings.timezone,
    enable_utc=False,
    task_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Si Redis no responde, no trabar el request: el evento ya está en el outbox y lo reenvía el relay.
    task_publish_retry_policy={"max_retries": 2, "interval_start": 0, "interval_step": 0.2, "interval_max": 0.5},
    beat_schedule={
        # Apertura nocturna: genera la grilla del siguiente día hábil y avisa a los alumnos.
        "apertura-turnos": {
            "task": "pilates.open_next_day",
            "schedule": crontab(
                hour=settings.booking_opening_time.hour,
                minute=settings.booking_opening_time.minute,
                day_of_week="mon-fri",
            ),
        },
        "recordatorios": {"task": "pilates.send_reminders", "schedule": crontab(minute="*/10")},
        # Reenvía los eventos del outbox cuyo aviso se perdió (Redis caído, worker reiniciado).
        "outbox-relay": {"task": "pilates.relay_outbox", "schedule": crontab(minute="*")},
    },
)
