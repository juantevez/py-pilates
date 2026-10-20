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
    # El lugar pasa solo al primero de la lista de espera.
    assert [m.subject for m in outbox.outbox if m.to == "a4@test.com"] == [
        "¡Tenés lugar! Te reservamos la clase del martes 13 de octubre 09:00 hs"
    ]
    nine = next(c for c in day_of(api.client.get("/clases/disponibles", headers=students[4]).json(), TUESDAY)["clases"]
                if c["start_time"].startswith("09:00"))
    assert nine["mi_turno_id"] and not nine["en_lista_espera"] and nine["completa"]
    again = api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=students[4])
    assert again.json()["error"] == "already_booked"
    # Re-reserva tras cancelar reutiliza la fila (restricción única user+clase).
    full_again = api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=students[0])
    assert full_again.json()["error"] == "class_full"


def test_waitlist_priority_skips_students_at_their_limit(api: Api, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    ana, beto, caro = api.user("ana@test.com", "Ana"), api.user("beto@test.com", "Beto"), api.user("caro@test.com", "Caro")
    holders = [api.user(f"h{i}@test.com") for i in range(4)]
    turnos = [api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=h).json()["id"] for h in holders]
    for h in (ana, beto, caro):  # en este orden
        assert api.client.post(f"/clases/{ids['10:00']}/lista-espera", headers=h).status_code == 201
    # Ana ya tiene su clase de la mañana: no puede tomar otra ese bloque.
    assert api.client.post("/turnos", json={"clase_id": ids["08:00"]}, headers=ana).status_code == 201
    outbox.outbox.clear()

    api.client.post(f"/turnos/{turnos[0]}/cancelar", headers=holders[0])

    assert [m.to for m in outbox.outbox if m.subject.startswith("¡Tenés lugar!")] == ["beto@test.com"]
    assert not [m for m in outbox.outbox if "liberó" in m.subject]
    ten = lambda h: next(c for c in day_of(api.client.get("/clases/disponibles", headers=h).json(), TUESDAY)["clases"]  # noqa: E731
                         if c["start_time"].startswith("10:00"))
    assert ten(beto)["mi_turno_id"] and not ten(beto)["en_lista_espera"]
    assert ten(ana)["en_lista_espera"] and ten(caro)["en_lista_espera"]  # siguen anotadas
    mine = api.client.get("/turnos/mios", headers=beto).json()["proximas"]
    assert [t["start_time"][:5] for t in mine] == ["10:00"]

    # Otro lugar: Ana sigue con el límite, pasa a Caro.
    outbox.outbox.clear()
    api.client.post(f"/turnos/{turnos[1]}/cancelar", headers=holders[1])
    assert [m.to for m in outbox.outbox if m.subject.startswith("¡Tenés lugar!")] == ["caro@test.com"]


def test_last_hour_spot_notifies_whole_waitlist_without_assigning(api: Api, clock: FixedClock, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    holders = [api.user(f"h{i}@test.com") for i in range(4)]
    turnos = [api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=h).json()["id"] for h in holders]
    ana, beto = api.user("ana@test.com", "Ana"), api.user("beto@test.com", "Beto")
    for h in (ana, beto):
        api.client.post(f"/clases/{ids['09:00']}/lista-espera", headers=h)
    clock.set(2026, 10, 13, 8, 30)  # a media hora: un turno asignado ya no se podría cancelar
    outbox.outbox.clear()

    assert api.client.post(f"/admin/turnos/{turnos[0]}/cancelar", json={}, headers=admin).status_code == 200

    assert sorted(m.to for m in outbox.outbox if "liberó" in m.subject) == ["ana@test.com", "beto@test.com"]
    assert not [m for m in outbox.outbox if m.subject.startswith("¡Tenés lugar!")]
    assert api.client.get("/turnos/mios", headers=ana).json()["proximas"] == []
    assert api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=beto).status_code == 201


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


def test_nightly_job_skips_closed_day(api: Api, container, clock: FixedClock, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    api.user("ana@test.com", "Ana")
    clock.set(2026, 10, 12, 19, 0)  # el cierre se carga antes de la apertura: el aviso lo da el job
    api.client.post("/admin/cierres", json={"desde": TUESDAY, "tipo": "feriado_nacional", "motivo": "Feriado"}, headers=admin)
    assert outbox.outbox == []
    # Lunes 20:00, martes cerrado: abre el miércoles y avisa las dos cosas.
    clock.set(2026, 10, 12, 20, 0)
    result = container.generate_daily_classes.execute(notify=True)
    assert result.estado == "generada" and result.fecha == date(2026, 10, 14) and result.clases_creadas == 7
    subjects = [m.subject for m in outbox.outbox if m.to == "ana@test.com"]
    assert "Sin clases el martes 13 de octubre" in subjects
    assert "Ya podés reservar tus turnos del miércoles 14 de octubre" in subjects

    # El martes (cerrado) el job vuelve a correr: no repite los avisos.
    outbox.outbox.clear()
    clock.set(2026, 10, 13, 20, 0)
    assert container.generate_daily_classes.execute(notify=True).fecha == date(2026, 10, 14)
    assert outbox.outbox == []


def test_closed_tomorrow_opens_next_open_day(api: Api, clock: FixedClock):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    h = api.user("ana@test.com")
    api.client.post("/admin/cierres", json={"desde": TUESDAY, "tipo": "feriado_nacional", "motivo": "Feriado"}, headers=admin)
    api.client.post("/admin/clases/generar", json={"fecha": "2026-10-14"}, headers=admin)
    wed = api.client.get("/admin/clases", params={"fecha": "2026-10-14"}, headers=admin).json()[0]["id"]

    clock.set(2026, 10, 12, 19, 0)  # lunes antes de la apertura
    days = api.client.get("/clases/disponibles", headers=h).json()
    assert [(d["fecha"], d["estado"]) for d in days] == [
        ("2026-10-12", "sin_clases"), (TUESDAY, "cerrado"), ("2026-10-14", "pendiente_apertura"),
    ]
    assert days[2]["apertura"].startswith("2026-10-12T20:00")
    assert api.client.post("/turnos", json={"clase_id": wed}, headers=h).json()["error"] == "outside_booking_window"

    clock.set(2026, 10, 12, 20, 0)  # lunes 20:00: se habilita el miércoles
    days = api.client.get("/clases/disponibles", headers=h).json()
    assert days[-1]["fecha"] == "2026-10-14" and days[-1]["estado"] == "abierta"
    assert api.client.post("/turnos", json={"clase_id": wed}, headers=h).status_code == 201


def test_closing_an_already_open_day_notifies_everyone_and_opens_the_next(api: Api, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)  # lunes 21:00: el martes ya está habilitado
    ana, beto = api.user("ana@test.com", "Ana"), api.user("beto@test.com", "Beto")
    api.client.post("/turnos", json={"clase_id": ids["09:00"]}, headers=ana)
    outbox.outbox.clear()

    r = api.client.post("/admin/cierres", json={"desde": TUESDAY, "tipo": "mantenimiento", "motivo": "Corte de luz"},
                        headers=admin)
    assert r.status_code == 201, r.text

    to = lambda who: [m.subject for m in outbox.outbox if m.to == who]  # noqa: E731
    # Ana pierde su turno: recibe la cancelación, no además el aviso general.
    assert "Clase cancelada: martes 13 de octubre 09:00 hs" in to("ana@test.com")
    assert "Sin clases el martes 13 de octubre" not in to("ana@test.com")
    # Beto no tenía turno: se entera del cierre y de que ya puede reservar el miércoles.
    assert "Sin clases el martes 13 de octubre" in to("beto@test.com")
    assert "Ya podés reservar tus turnos del miércoles 14 de octubre" in to("beto@test.com")

    wed = day_of(api.client.get("/clases/disponibles", headers=beto).json(), "2026-10-14")
    assert wed["estado"] == "abierta" and len(wed["clases"]) == 7
    assert api.client.post("/turnos", json={"clase_id": wed["clases"][0]["clase_id"]}, headers=beto).status_code == 201

    # Un cierre de un día que todavía no se habilitó no avisa nada ahora (lo hace el job).
    outbox.outbox.clear()
    api.client.post("/admin/cierres", json={"desde": "2026-10-16", "tipo": "otro"}, headers=admin)
    assert outbox.outbox == []


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
    assert container.send_reminders.execute() == 0  # recién reservó: nada de recordatorio a la noche
    clock.set(2026, 10, 13, 7, 0)
    assert container.send_reminders.execute() == 1  # clase de las 08:00: se recuerda a las 07:00
    assert container.send_reminders.execute() == 0

    clock.set(2026, 10, 12, 21, 30)
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

    settings = Settings(database_url=TEST_DB_URL, event_bus="sync", email_backend="console", jwt_secret="x" * 32, rate_limit_storage="memory",
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


def test_admin_creates_user_who_sets_password(api: Api, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    student = api.user("ana@test.com")
    body = {"email": "nueva@test.com", "full_name": "Nueva Alumna"}
    assert api.client.post("/admin/usuarios", json=body, headers=student).status_code == 403

    r = api.client.post("/admin/usuarios", json=body, headers=admin)
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "alumno" and r.json()["status"] == "activo"
    assert api.client.post("/admin/usuarios", json=body, headers=admin).json()["error"] == "email_taken"

    mail = next(m for m in outbox.outbox if m.to == "nueva@test.com")
    assert "Bienvenido" in mail.subject
    token = mail.body.split("token=")[1].split()[0]
    r = api.client.post("/auth/reset-password", json={"token": token, "new_password": "mi-clave-1"})
    assert r.status_code == 204
    login = api.client.post("/auth/login", json={"email": "nueva@test.com", "password": "mi-clave-1"})
    assert login.status_code == 200


def test_admin_books_for_student_only_capacity_applies(api: Api, clock: FixedClock, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    h = api.user("ana@test.com", "Ana")
    ana = api.client.get("/auth/me", headers=h).json()["id"]
    assert api.client.post("/admin/turnos", json={"user_id": ana, "clase_id": ids["08:00"]}, headers=h).status_code == 403

    # Saltea el límite por bloque (08:00 y 09:00 son ambas de la mañana) y avisa al alumno.
    outbox.outbox.clear()
    for hora in ("08:00", "09:00"):
        r = api.client.post("/admin/turnos", json={"user_id": ana, "clase_id": ids[hora]}, headers=admin)
        assert r.status_code == 201, r.text
        assert r.json()["booked_by"] == "gimnasio"
    assert [m.to for m in outbox.outbox if "Reserva confirmada" in m.subject] == ["ana@test.com", "ana@test.com"]
    dup = api.client.post("/admin/turnos", json={"user_id": ana, "clase_id": ids["08:00"]}, headers=admin)
    assert dup.json()["error"] == "already_booked"

    # Saltea la ventana de reserva: el miércoles todavía no abrió para los alumnos.
    r = api.client.post("/admin/clases/generar", json={"fecha": "2026-10-14"}, headers=admin)
    assert r.status_code == 200, r.text
    wed = api.client.get("/admin/clases", params={"fecha": "2026-10-14"}, headers=admin).json()[0]["id"]
    assert api.client.post("/turnos", json={"clase_id": wed}, headers=h).json()["error"] == "outside_booking_window"
    assert api.client.post("/admin/turnos", json={"user_id": ana, "clase_id": wed}, headers=admin).status_code == 201

    # Saltea el cierre de reservas de una clase en curso, pero no la de una que ya terminó.
    clock.set(2026, 10, 13, 15, 30)
    bea = api.client.get("/auth/me", headers=api.user("bea@test.com")).json()["id"]
    assert api.client.post("/admin/turnos", json={"user_id": bea, "clase_id": ids["15:00"]}, headers=admin).status_code == 201
    r = api.client.post("/admin/turnos", json={"user_id": bea, "clase_id": ids["11:00"]}, headers=admin)
    assert r.status_code == 422 and r.json()["error"] == "class_finished"

    # El cupo sí se respeta: la 16:00 tiene 4 lugares.
    others = [api.client.get("/auth/me", headers=api.user(f"x{i}@test.com")).json()["id"] for i in range(5)]
    codes = [
        api.client.post("/admin/turnos", json={"user_id": u, "clase_id": ids["16:00"]}, headers=admin).status_code
        for u in others
    ]
    assert codes == [201, 201, 201, 201, 409]

    mine = api.client.get("/turnos/mios", headers=h).json()
    assert len(mine["proximas"]) + len(mine["pasadas"]) == 3


def test_admin_cancels_single_booking(api: Api, clock: FixedClock, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    ana, bea, cami = api.user("ana@test.com", "Ana"), api.user("bea@test.com", "Bea"), api.user("cami@test.com", "Cami")
    turno = api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=ana).json()
    for i in range(3):
        assert api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=api.user(f"x{i}@test.com")).status_code == 201
    assert api.client.post(f"/clases/{ids['10:00']}/lista-espera", headers=cami).status_code == 201

    assert api.client.post(f"/admin/turnos/{turno['id']}/cancelar", json={}, headers=ana).status_code == 403

    # Sin ventana de anticipación: a las 09:30 el alumno ya no podría cancelar la de las 10:00.
    clock.set(2026, 10, 13, 9, 30)
    outbox.outbox.clear()
    r = api.client.post(f"/admin/turnos/{turno['id']}/cancelar", json={"motivo": "Pidió cambio"}, headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelada_por_admin"
    to_ana = [m for m in outbox.outbox if m.to == "ana@test.com"]
    assert [m.subject for m in to_ana] == ["Turno cancelado: martes 13 de octubre 10:00 hs"]
    assert "Pidió cambio" in to_ana[0].body
    assert [m.to for m in outbox.outbox if "liberó" in m.subject] == ["cami@test.com"]

    again = api.client.post(f"/admin/turnos/{turno['id']}/cancelar", json={}, headers=admin)
    assert again.json()["error"] == "booking_not_active"
    mine = api.client.get("/turnos/mios", headers=ana).json()
    assert mine["proximas"][0]["status"] == "cancelada_por_admin"

    # Una clase terminada no se toca.
    other = api.client.post("/turnos", json={"clase_id": ids["11:00"]}, headers=bea).json()
    clock.set(2026, 10, 13, 12, 0)
    r = api.client.post(f"/admin/turnos/{other['id']}/cancelar", json={}, headers=admin)
    assert r.json()["error"] == "class_finished"


def test_reactivated_class_only_notifies_bookings_cancelled_with_class(api: Api, outbox):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    ana, bea = api.user("ana@test.com", "Ana"), api.user("bea@test.com", "Bea")
    turno_ana = api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=ana).json()
    api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=bea)

    api.client.post(f"/admin/turnos/{turno_ana['id']}/cancelar", json={}, headers=admin)
    api.client.post(f"/admin/clases/{ids['10:00']}/cancelar", json={"motivo": "gripe"}, headers=admin)
    outbox.outbox.clear()
    assert api.client.post(f"/admin/clases/{ids['10:00']}/reactivar", headers=admin).status_code == 204
    assert [m.to for m in outbox.outbox if "Vuelve la clase" in m.subject] == ["bea@test.com"]


def test_marked_attendance_still_takes_a_spot(api: Api, clock: FixedClock):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    students = [api.user(f"a{i}@test.com", f"Alumno {i}") for i in range(4)]
    turnos = [api.client.post("/turnos", json={"clase_id": ids["16:00"]}, headers=h).json()["id"] for h in students]

    # Clase en curso: el admin marca asistencia y ausencia; esos lugares siguen ocupados.
    clock.set(2026, 10, 13, 16, 15)
    api.client.post(f"/admin/turnos/{turnos[0]}/asistencia", json={"asistio": True}, headers=admin)
    api.client.post(f"/admin/turnos/{turnos[1]}/asistencia", json={"asistio": False}, headers=admin)
    extra = api.client.get("/auth/me", headers=api.user("extra@test.com")).json()["id"]
    r = api.client.post("/admin/turnos", json={"user_id": extra, "clase_id": ids["16:00"]}, headers=admin)
    assert r.status_code == 409 and r.json()["error"] == "class_full"

    nine = next(c for c in api.client.get("/admin/clases", params={"fecha": TUESDAY}, headers=admin).json() if c["id"] == ids["16:00"])
    assert nine["reservados"] == 4
    r = api.client.patch(f"/admin/clases/{ids['16:00']}", json={"max_capacity": 3}, headers=admin)
    assert r.json()["error"] == "capacity_below_bookings"


def test_marked_attendance_still_counts_for_daily_limits(api: Api, clock: FixedClock):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    ids = open_tuesday(api, admin)
    h = api.user("ana@test.com")
    turno = api.client.post("/turnos", json={"clase_id": ids["08:00"]}, headers=h).json()["id"]

    # Mismo día, con la clase de las 08:00 en curso y la asistencia ya marcada.
    clock.set(2026, 10, 13, 8, 30)
    api.client.post(f"/admin/turnos/{turno}/asistencia", json={"asistio": True}, headers=admin)
    r = api.client.post("/turnos", json={"clase_id": ids["10:00"]}, headers=h)
    assert r.json()["error"] == "block_limit_reached"
    assert api.client.post("/turnos", json={"clase_id": ids["15:00"]}, headers=h).status_code == 201
    r = api.client.post("/turnos", json={"clase_id": ids["16:00"]}, headers=h)
    assert r.json()["error"] == "daily_limit_reached"
