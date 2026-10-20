from uuid import UUID

import jwt

from pilates.identity.domain.model import Role
from tests.conftest import Api


def login(api: Api, email: str = "ana@test.com", password: str = "secreta123") -> dict:
    r = api.client.post("/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def me(api: Api, headers: dict):
    return api.client.get("/auth/me", headers=headers)


def test_logout_closes_only_this_device(api: Api):
    api.user("ana@test.com", "Ana")
    phone, pc = login(api), login(api)

    assert api.client.post("/auth/logout", headers=pc).status_code == 204

    r = me(api, pc)
    assert r.status_code == 401 and r.json()["error"] == "session_revoked"
    assert me(api, phone).status_code == 200  # el otro dispositivo sigue
    assert api.client.post("/auth/logout", headers=pc).status_code == 401


def test_password_reset_closes_every_session(api: Api, outbox):
    api.user("ana@test.com", "Ana")
    phone, pc = login(api), login(api)
    api.client.post("/auth/forgot-password", json={"email": "ana@test.com"})
    token = next(m for m in outbox.outbox if m.to == "ana@test.com").body.split("token=")[1].split()[0]
    assert api.client.post("/auth/reset-password", json={"token": token, "new_password": "nueva-clave-1"}).status_code == 204

    assert me(api, phone).json()["error"] == "session_revoked"
    assert me(api, pc).json()["error"] == "session_revoked"
    assert me(api, login(api, password="nueva-clave-1")).status_code == 200


def test_blocking_closes_every_session_and_unblocking_does_not_reopen_them(api: Api):
    admin = api.user("admin@test.com", role=Role.ADMIN)
    api.user("ana@test.com", "Ana")
    ana = login(api)
    user_id = me(api, ana).json()["id"]

    api.client.post(f"/admin/usuarios/{user_id}/bloquear", json={"motivo": "Deuda"}, headers=admin)
    assert me(api, ana).status_code == 401
    api.client.post(f"/admin/usuarios/{user_id}/desbloquear", headers=admin)
    assert me(api, ana).json()["error"] == "session_revoked"  # tiene que volver a ingresar
    assert me(api, login(api)).status_code == 200


def test_tokens_without_session_are_rejected(api: Api, container):
    """Los tokens emitidos antes de las sesiones revocables (sin ``sid``) dejan de valer."""
    api.user("ana@test.com", "Ana")
    user_id = me(api, login(api)).json()["id"]
    legacy = jwt.encode({"sub": user_id, "role": "alumno", "exp": 4102444800}, container.settings.jwt_secret)
    r = me(api, {"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 401 and r.json()["error"] == "invalid_token"


def test_session_id_from_another_user_is_rejected(api: Api, container):
    api.user("ana@test.com", "Ana")
    api.user("beto@test.com", "Beto")
    ana_claims = container.token_service.decode(login(api)["Authorization"].split()[1])
    beto_id = me(api, login(api, "beto@test.com")).json()["id"]
    forged = container.token_service.issue(user_id=UUID(beto_id), role="alumno", session_id=ana_claims.session_id)
    assert me(api, {"Authorization": f"Bearer {forged.token}"}).json()["error"] == "session_revoked"
