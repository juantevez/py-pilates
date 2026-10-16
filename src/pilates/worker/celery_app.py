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
    },
)
