from datetime import date

from pilates.identity.domain.model import Role
from tests.conftest import Api, FixedClock

TUESDAY = "2026-10-13"


def day_of(days: list[dict], fecha: str) -> dict:
    return next(d for d in days if d["fecha"] == fecha)


def open_tuesday(api: Api, admin: dict) -> dict[str, str]:
    r = api.client.post("/admin/clases/generar", json={"fecha": TUESDAY}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["clases_creadas"] == 7
    classes = api.client.get("/admin/clases", params={"fecha": TUESDAY}, headers=admin).json()
    return {c["start_time"][:5]: c["id"] for c in classes}


def test_register_login_and_me(api: Api):
    h = api.user("ana@test.com", "Ana")
    me = api.client.get("/auth/me", headers=h)
    assert me.json()["email"] == "ana@test.com"
    assert api.client.get("/auth/me").status_code == 401
    assert api.client.get("/admin/usuarios", headers=h).status_code == 403


def test_availability_before_opening(api: Api, clock: FixedClock):
    clock.set(2026, 10, 12, 10, 0)
    h = api.user("ana@test.com")
    days = api.client.get("/clases/disponibles", headers=h).json()
    assert day_of(days, TUESDAY)["estado"] == "pendiente_apertura"


def test_capacity_waitlist_and_release(api: Api, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    students = [api.user(f"a{i}@test.com", f"Alumno {i}") for i in range(5)]

    turnos = []
    for h in students[:4]:
        r = api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=h)
        assert r.status_code == 201, r.text
        turnos.append(r.json()["id"])

    full = api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=students[4])
    assert full.status_code == 409 and full.json()["error"] == "class_full"

    grid = day_of(api.client.get("/clases/disponibles", headers=students[4]).json(), TUESDAY)
    nine = next(c for c in grid["clases"] if c["start_time"].startswith("09:00"))
    assert nine["completa"] and nine["puede_anotarse_lista_espera"] and not nine["puede_reservar"]

    assert api.client.post(f"/clases/{ids['09:00']}/lista-espera", headers=students[4]).status_code == 201
    outbox.outbox.clear()
    r = api.client.post(f"/turnos/{turnos[0]}/cancelar", headers=students[0])
    assert r.status_code == 200 and r.json()["status"] == "cancelada"
    assert [m.to for m in outbox.outbox if "liberó" in m.subject] == ["a4@test.com"]

    r = api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=students[4])
    assert r.status_code == 201
    # Re-reserva tras cancelar reutiliza la fila (restricción única user+clase).
    full_again = api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=students[0])
    assert full_again.json()["error"] == "class_full"


def test_per_user_limits(api: Api):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    h = api.user("ana@test.com")
    assert api.client.post("/turnos", json={"clase_id": ids["08:00"]}, headers=h).status_code == 201
    r = api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=h)
    assert r.json()["error"] == "block_limit_reached"
    assert api.client.post("/turnos", json={"clase_id": ids["15:00"]}, headers=h).status_code == 201
    dup = api.client.post("/turnos", json={"clase_id": ids["15:00"]}, headers=h)
    assert dup.json()["error"] == "already_booked"


def test_booking_window_and_cancellation_deadline(api: Api, clock: FixedClock):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    h = api.user("ana@test.com")
    turno = api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=h).json()

    clock.set(2026, 10, 13, 8, 30)  # martes 08:30: faltan 30 min
    r = api.client.post(f"/turnos/{turno['id']}/cancelar", headers=h)
    assert r.status_code == 422 and r.json()["error"] == "cancellation_window_closed"
    # Mismo día: una clase que ya empezó no se puede reservar; una que no empezó, sí.
    other = api.user("bea@test.com")
    r = api.client.post("/turnos", json={"clase_id": ids["08:00"]}, headers=other)
    assert r.json()["error"] == "booking_closed"
    r = api.client.post("/turnos", json={"clase_id": ids["16:00"]}, headers=other)
    assert r.status_code == 201
    # El miércoles todavía no abrió (abre el martes 20:00) -> fuera de horizonte.
    days = api.client.get("/clases/disponibles", headers=other).json()
    assert [(d["fecha"], d["estado"]) for d in days] == [(TUESDAY, "abierta"), ("2026-10-14", "pendiente_apertura")]

    mine = api.client.get("/turnos/mios", headers=h).json()
    assert len(mine["proximas"]) == 1 and mine["proximas"][0]["puede_cancelar"] is False


def test_admin_cancels_class_notifies_students(api: Api, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    h = api.user("ana@test.com", "Ana")
    api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=h)
    r = api.client.post(f"/admin/clases/{ids['10:00']}/cancelar", json={"motivo": "gripe del instructor"}, headers=admin)
    assert r.json() == {"turnos_cancelados": 1}
    assert any(m.to == "ana@test.com" and "cancelada" in m.subject for m in outbox.outbox)
    mine = api.client.get("/turnos/mios", headers=h).json()
    assert mine["proximas"][0]["status"] == "cancelada_por_admin"


def test_closure_cancels_bookings_and_blocks_day(api: Api, clock: FixedClock, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    h = api.user("ana@test.com", "Ana")
    api.client.post("/turnos", json={"clase_id": ids["11:00"]}, headers=h)

    r = api.client.post(
        "/admin/cierres", json={"desde": TUESDAY, "tipo": "mantenimiento", "motivo": "Corte de luz"}, headers=admin
    )
    assert r.status_code == 201, r.text
    cierre_id = r.json()[0]["id"]
    assert any("Corte de luz" in m.body for m in outbox.outbox if m.to == "ana@test.com")

    day = day_of(api.client.get("/clases/disponibles", headers=h).json(), TUESDAY)
    assert day["estado"] == "cerrado" and day["motivo_cierre"] == "Corte de luz"
    assert day["proximo_dia_habil"] == "2026-10-14"

    dup = api.client.post("/admin/cierres", json={"desde": TUESDAY, "tipo": "otro"}, headers=admin)
    assert dup.status_code == 409

    assert api.client.delete(f"/admin/cierres/{cierre_id}", headers=admin).status_code == 204
    classes = api.client.get("/admin/clases", params={"fecha": TUESDAY}, headers=admin).json()
    assert all(c["status"] == "activa" for c in classes)

    cal = api.client.get("/admin/calendario", params={"desde": "2026-10-12", "hasta": "2026-10-18"}, headers=admin)
    assert [d["es_dia_habil"] for d in cal.json()] == [True] * 5 + [False] * 2


def test_nightly_job_skips_closed_day(api: Api, container, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    api.user("ana@test.com", "Ana")
    api.client.post("/admin/cierres", json={"desde": TUESDAY, "tipo": "feriado_nacional", "motivo": "Feriado"}, headers=admin)
    result = container.generate_daily_classes.execute(notify=True)
    assert result.estado == "cerrado" and result.fecha == date(2026, 10, 13)
    assert any(m.to == "ana@test.com" and "Sin clases" in m.subject for m in outbox.outbox)


def test_nightly_job_opens_and_notifies(api: Api, container, outbox):
    api.user("ana@test.com", "Ana")
    result = container.generate_daily_classes.execute(notify=True)
    assert result.estado == "generada" and result.clases_creadas == 7
    assert any(m.to == "ana@test.com" and "Ya podés reservar" in m.subject for m in outbox.outbox)
    assert container.generate_daily_classes.execute().clases_creadas == 0  # idempotente


def test_attendance_and_reminders(api: Api, clock: FixedClock, container, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    h = api.user("ana@test.com", "Ana")
    turno = api.client.post("/turnos", json={"clase_id": ids["08:00"]}, headers=h).json()

    clock.set(2026, 10, 12, 21, 30)
    assert container.send_reminders.execute() == 1  # clase a las 08:00, faltan < 12 h
    assert container.send_reminders.execute() == 0

    early = api.client.post(f"/admin/turnos/{turno['id']}/asistencia", json={"asistio": True}, headers=admin)
    assert early.json()["error"] == "class_not_started"
    clock.set(2026, 10, 13, 8, 5)
    sheet = api.client.get("/admin/asistencia", headers=admin).json()
    assert sheet[0]["alumnos"][0]["full_name"] == "Ana"
    r = api.client.post(f"/admin/turnos/{turno['id']}/asistencia", json={"asistio": False}, headers=admin)
    assert r.json()["status"] == "no_asistio"
    users = api.client.get("/admin/usuarios", params={"search": "ana"}, headers=admin).json()
    assert users[0]["ausencias"] == 1


def test_block_user_cancels_future_bookings(api: Api):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    h = api.user("ana@test.com", "Ana")
    api.client.post("/turnos", json={"clase_id": ids["08:00"]}, headers=h)
    user_id = api.client.get("/auth/me", headers=h).json()["id"]

    r = api.client.post(f"/admin/usuarios/{user_id}/bloquear", json={"motivo": "Moroso"}, headers=admin)
    assert r.json()["status"] == "bloqueado"
    assert api.client.get("/auth/me", headers=h).status_code == 403
    classes = api.client.get("/admin/clases", params={"fecha": TUESDAY}, headers=admin).json()
    assert next(c for c in classes if c["id"] == ids["08:00"])["reservados"] == 0


def test_password_reset_flow(api: Api, outbox):
    api.user("ana@test.com", "Ana")
    assert api.client.post("/auth/forgot-password", json={"email": "nadie@test.com"}).status_code == 202
    assert api.client.post("/auth/forgot-password", json={"email": "ana@test.com"}).status_code == 202
    mail = next(m for m in outbox.outbox if m.to == "ana@test.com")
    token = mail.body.split("token=")[1].split()[0]

    r = api.client.post("/auth/reset-password", json={"token": token, "new_password": "nueva-clave-1"})
    assert r.status_code == 204
    again = api.client.post("/auth/reset-password", json={"token": token, "new_password": "otra-clave-2"})
    assert again.json()["error"] == "invalid_reset_token"
    login = api.client.post("/auth/login", json={"email": "ana@test.com", "password": "nueva-clave-1"})
    assert login.status_code == 200


def test_student_calendar_shows_closures(api: Api):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    h = api.user("ana@test.com", "Ana")
    api.client.post(
        "/admin/cierres",
        json={"desde": "2026-10-14", "hasta": "2026-10-15", "tipo": "vacaciones_gym", "motivo": "Vacaciones"},
        headers=admin,
    )
    api.client.post("/admin/cierres", json={"desde": "2026-10-16", "tipo": "feriado_nacional"}, headers=admin)

    days = api.client.get("/calendario", params={"desde": "2026-10-13", "hasta": "2026-10-18"}, headers=h).json()
    by_date = {d["fecha"]: d for d in days}
    assert by_date["2026-10-13"]["hay_clases"] is True
    assert by_date["2026-10-14"] == {
        "fecha": "2026-10-14", "hay_clases": False, "tipo_cierre": "vacaciones_gym",
        "motivo": "Vacaciones", "etiqueta": "Vacaciones del estudio - Sin clases",
    }
    assert by_date["2026-10-16"]["etiqueta"] == "Feriado - Sin clases"
    assert by_date["2026-10-17"]["hay_clases"] is False and by_date["2026-10-17"]["tipo_cierre"] is None
    assert "creado_por" not in by_date["2026-10-14"]

    default = api.client.get("/calendario", headers=h).json()
    assert default[0]["fecha"] == "2026-10-12" and len(default) == 31
    assert api.client.get("/calendario").status_code == 401
    bad = api.client.get("/calendario", params={"desde": "2026-10-13", "hasta": "2027-03-01"}, headers=h)
    assert bad.json()["error"] == "invalid_range"


def test_same_day_booking_can_be_disabled(engine, clock, outbox):
    from pilates.bootstrap import build_container
    from pilates.config import Settings
    from pilates.shared.domain.errors import BusinessRuleViolation
    from tests.conftest import TEST_DB_URL

    settings = Settings(database_url=TEST_DB_URL, event_bus="sync", email_backend="console", jwt_secret="x" * 32,
                        allow_same_day_booking=False, _env_file=None)
    c = build_container(settings, engine=engine, clock=clock, email_sender=outbox)
    c.generate_daily_classes.execute(fecha=date(2026, 10, 13))
    clase = c.list_classes_for_date.execute(fecha=date(2026, 10, 13))[-1]
    user = c.register_user.execute(email="solo-manana@test.com", full_name="X", password="secreta123", role=Role.ALUMNO)
    clock.set(2026, 10, 13, 9, 0)
    try:
        c.book_class.execute(user_id=user.id, clase_id=clase.id)
        raise AssertionError("debió rechazarse")
    except BusinessRuleViolation as e:
        assert e.code == "outside_booking_window"


def test_reactivate_cancelled_class(api: Api, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    ana = api.user("ana@test.com", "Ana")
    bea = api.user("bea@test.com", "Bea")
    api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=ana)
    api.client.post(f"/admin/clases/{ids['10:00']}/cancelar", json={"motivo": "gripe"}, headers=admin)
    assert api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=bea).json()["error"] == "class_not_available"

    outbox.outbox.clear()
    assert api.client.post(f"/admin/clases/{ids['10:00']}/reactivar", headers=admin).status_code == 204
    reopened = [m for m in outbox.outbox if "Vuelve la clase" in m.subject]
    assert [m.to for m in reopened] == ["ana@test.com"]  # sólo quien perdió el turno
    # La reserva anterior no se restaura; ahora cualquiera puede reservar.
    assert api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=bea).status_code == 201
    assert api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=ana).status_code == 201

    again = api.client.post(f"/admin/clases/{ids['10:00']}/reactivar", headers=admin)
    assert again.json()["error"] == "class_already_active"


def test_reactivate_rejects_closed_day(api: Api):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    api.client.post(f"/admin/clases/{ids['08:00']}/cancelar", json={"motivo": "x"}, headers=admin)
    api.client.post("/admin/cierres", json={"desde": TUESDAY, "tipo": "otro"}, headers=admin)
    r = api.client.post(f"/admin/clases/{ids['08:00']}/reactivar", headers=admin)
    assert r.json()["error"] == "day_closed"


def test_reopen_vacation_range(api: Api, clock: FixedClock, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    ana = api.user("ana@test.com", "Ana")
    api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=ana)
    api.client.post(
        "/admin/cierres",
        json={"desde": TUESDAY, "hasta": "2026-10-16", "tipo": "vacaciones_gym", "motivo": "Vacaciones"},
        headers=admin,
    )
    assert all(c["status"] == "feriado" for c in api.client.get("/admin/clases", params={"fecha": TUESDAY}, headers=admin).json())

    outbox.outbox.clear()
    r = api.client.request("DELETE", "/admin/cierres", params={"desde": TUESDAY, "hasta": "2026-10-16"}, headers=admin)
    assert r.json() == {"cierres_eliminados": 4, "clases_reabiertas": 7}
    assert all(c["status"] == "activa" for c in api.client.get("/admin/clases", params={"fecha": TUESDAY}, headers=admin).json())
    assert [m.to for m in outbox.outbox if "Vuelve la clase" in m.subject] == ["ana@test.com"]
    assert api.client.get("/calendario", params={"desde": TUESDAY, "hasta": "2026-10-16"}, headers=ana).json()[1]["hay_clases"]

    empty = api.client.request("DELETE", "/admin/cierres", params={"desde": TUESDAY, "hasta": "2026-10-16"}, headers=admin)
    assert empty.status_code == 404
