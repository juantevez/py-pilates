"""Comandos de administración: `python -m pilates.cli create-admin | open-next-day | send-reminders`."""

import sys

from pilates.bootstrap import build_container
from pilates.config import get_settings
from pilates.identity.domain.model import Role
from pilates.shared.domain.errors import ConflictError


def create_admin() -> None:
    settings = get_settings()
    if not settings.admin_email or not settings.admin_password:
        print("ADMIN_EMAIL / ADMIN_PASSWORD no configurados; se omite la creación del admin.")
        return
    c = build_container(settings)
    try:
        user = c.register_user.execute(
            email=settings.admin_email, full_name=settings.admin_name, password=settings.admin_password, role=Role.ADMIN
        )
        print(f"Admin creado: {user.email}")
    except ConflictError:
        print(f"El admin {settings.admin_email} ya existe.")


def open_next_day() -> None:
    print(build_container(get_settings()).generate_daily_classes.execute(notify=True))


def send_reminders() -> None:
    print(f"Recordatorios enviados: {build_container(get_settings()).send_reminders.execute()}")


COMMANDS = {"create-admin": create_admin, "open-next-day": open_next_day, "send-reminders": send_reminders}

if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in COMMANDS:
        sys.exit(f"Uso: python -m pilates.cli [{' | '.join(COMMANDS)}]")
    COMMANDS[sys.argv[1]]()
