import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from pilates.bootstrap import Container, build_container
from pilates.config import Settings
from pilates.identity.domain.model import Role
from pilates.main import create_app
from pilates.notifications.adapters.email import ConsoleEmailSender
from pilates.shared.application.ports import Clock

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "postgresql+psycopg://pilates:pilates@localhost:5432/pilates_test")
TABLES = "outbox, admin_audit_log, lista_espera, turnos, cierres_gimnasio, clases, password_reset_tokens, users"


class FixedClock(Clock):
    def __init__(self, now: datetime) -> None:
        self.current = now

    def now(self) -> datetime:
        return self.current

    def set(self, *args: int) -> None:
        self.current = datetime(*args, tzinfo=TZ)


@pytest.fixture(scope="session")
def engine():
    url = make_url(TEST_DB_URL)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            if not conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database}).scalar():
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except Exception as e:  # pragma: no cover
        pytest.skip(f"PostgreSQL no disponible para tests de integración: {e}")
    finally:
        admin.dispose()
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    command.upgrade(cfg, "head")
    eng = create_engine(TEST_DB_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 10, 12, 21, 0, tzinfo=TZ))  # lunes 21:00, ya abrió el martes


@pytest.fixture
def outbox() -> ConsoleEmailSender:
    return ConsoleEmailSender()


@pytest.fixture
def container(engine, clock, outbox) -> Container:
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    settings = Settings(
        database_url=TEST_DB_URL, event_bus="sync", email_backend="console", jwt_secret="test-secret-with-at-least-32-bytes!!",
        rate_limit_storage="memory", _env_file=None
    )
    return build_container(settings, engine=engine, clock=clock, email_sender=outbox)


@pytest.fixture
def client(container) -> TestClient:
    return TestClient(create_app(container))


class Api:
    def __init__(self, client: TestClient, container: Container) -> None:
        self.client, self.container = client, container

    def user(self, email: str, name: str = "Alumno", role: Role = Role.ALUMNO) -> dict:
        self.container.register_user.execute(email=email, full_name=name, password="secreta123", role=role)
        r = self.client.post("/auth/login", json={"email": email, "password": "secreta123"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def api(client, container) -> Api:
    return Api(client, container)
