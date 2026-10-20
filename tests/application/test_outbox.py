from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text

from pilates.identity.domain.model import Role

LATER = timedelta(minutes=3)  # pasado el margen del relay


class BrokerDown:
    def notify(self, message_ids):
        raise ConnectionError("redis no responde")


def pending(engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, event_name, handler_name, attempts, last_error FROM outbox WHERE processed_at IS NULL")
        )
        return [dict(r._mapping) for r in rows]


def book_tuesday(container, email: str):
    container.generate_daily_classes.execute(fecha=date(2026, 10, 13))
    clase = container.list_classes_for_date.execute(fecha=date(2026, 10, 13))[0]
    user = container.register_user.execute(email=email, full_name="Ana", password="secreta123", role=Role.ALUMNO)
    return container.book_class.execute(user_id=user.id, clase_id=clase.id)


def confirmations(outbox) -> list:
    return [m for m in outbox.outbox if m.subject.startswith("Reserva confirmada")]


def test_event_survives_broker_outage_and_relay_delivers_it(container, engine, outbox):
    real = container.outbox.notifier
    container.outbox.notifier = BrokerDown()

    turno = book_tuesday(container, "ana@test.com")

    # La reserva quedó hecha aunque no se pudo avisar al worker: el evento está en el outbox.
    assert turno.status == "confirmada"
    assert confirmations(outbox) == []
    [row] = pending(engine)
    assert (row["event_name"], row["handler_name"]) == ("TurnoReservado", "notifications.confirmacion")

    container.outbox.notifier = real
    # Dentro del margen el relay no lo toca (el aviso inmediato podría estar en camino).
    assert container.relay_outbox.execute() == 0
    assert container.relay_outbox.execute(now=datetime.now(UTC) + LATER) == 1
    assert len(confirmations(outbox)) == 1
    assert pending(engine) == []
    assert container.relay_outbox.execute(now=datetime.now(UTC) + LATER * 3) == 0


def test_failed_handler_stays_pending_and_dispatch_is_idempotent(container, engine, outbox, monkeypatch):
    send = outbox.send

    def smtp_down(message):
        raise OSError("SMTP caído")

    monkeypatch.setattr(outbox, "send", smtp_down)
    book_tuesday(container, "beto@test.com")

    [row] = pending(engine)
    assert row["attempts"] == 1 and "SMTP caído" in row["last_error"]

    monkeypatch.setattr(outbox, "send", send)
    assert container.relay_outbox.execute(now=datetime.now(UTC) + LATER) == 1
    assert len(confirmations(outbox)) == 1

    # Un aviso duplicado (relay + Celery) no vuelve a ejecutar el handler.
    assert container.outbox_dispatcher.dispatch(row["id"]) is False
    assert len(confirmations(outbox)) == 1


def test_rows_that_exhaust_retries_are_not_relayed(container, engine, outbox, monkeypatch):
    monkeypatch.setattr(outbox, "send", lambda message: (_ for _ in ()).throw(OSError("rebota")))
    book_tuesday(container, "caro@test.com")
    with engine.begin() as conn:
        conn.execute(text("UPDATE outbox SET attempts = 10"))
    assert container.relay_outbox.execute(now=datetime.now(UTC) + LATER) == 0
    assert len(pending(engine)) == 1


def fail_once_for(outbox, monkeypatch, address: str) -> None:
    send, failed = outbox.send, []

    def flaky(message):
        if message.to == address and not failed:
            failed.append(message)
            raise OSError("SMTP rechazó el envío")
        send(message)

    monkeypatch.setattr(outbox, "send", flaky)


def received(outbox, prefix: str) -> list[str]:
    return sorted(m.to for m in outbox.outbox if m.subject.startswith(prefix))


def test_mass_email_retry_only_resends_the_failed_recipient(container, engine, outbox, monkeypatch):
    for name in ("a", "b", "c"):
        container.register_user.execute(email=f"{name}@test.com", full_name=name, password="secreta123", role=Role.ALUMNO)
    fail_once_for(outbox, monkeypatch, "b@test.com")

    container.generate_daily_classes.execute(notify=True)  # apertura nocturna: un correo por alumno
    assert received(outbox, "Ya podés reservar") == ["a@test.com", "c@test.com"]
    [row] = pending(engine)
    assert row["handler_name"] == "notifications.enviar_correo"

    assert container.relay_outbox.execute(now=datetime.now(UTC) + LATER) == 1
    assert received(outbox, "Ya podés reservar") == ["a@test.com", "b@test.com", "c@test.com"]


def test_reminder_smtp_failure_does_not_resend_the_others(container, clock, outbox, monkeypatch):
    container.generate_daily_classes.execute(fecha=date(2026, 10, 13))
    clase = container.list_classes_for_date.execute(fecha=date(2026, 10, 13))[0]
    for name in ("a", "b"):
        user = container.register_user.execute(email=f"{name}@test.com", full_name=name, password="secreta123", role=Role.ALUMNO)
        container.book_class.execute(user_id=user.id, clase_id=clase.id)
    fail_once_for(outbox, monkeypatch, "a@test.com")

    clock.set(2026, 10, 12, 21, 30)
    assert container.send_reminders.execute() == 2
    assert received(outbox, "Recordatorio") == ["b@test.com"]
    assert container.send_reminders.execute() == 0  # los dos quedaron marcados

    container.relay_outbox.execute(now=datetime.now(UTC) + LATER)
    assert received(outbox, "Recordatorio") == ["a@test.com", "b@test.com"]
