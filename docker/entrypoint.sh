#!/bin/sh
set -e

case "$1" in
  api)
    alembic upgrade head
    python -m pilates.cli create-admin
    exec uvicorn --factory pilates.main:create_app --host 0.0.0.0 --port 8000 --proxy-headers
    ;;
  worker)
    exec celery -A pilates.worker.celery_app:celery_app worker --loglevel="${LOG_LEVEL:-INFO}" --concurrency="${WORKER_CONCURRENCY:-2}"
    ;;
  beat)
    exec celery -A pilates.worker.celery_app:celery_app beat --loglevel="${LOG_LEVEL:-INFO}" --schedule=/tmp/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac
