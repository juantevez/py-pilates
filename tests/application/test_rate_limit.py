import time

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from pilates.bootstrap import build_container
from pilates.config import Settings
from pilates.identity.domain.model import Role
from pilates.main import create_app
from pilates.shared.infrastructure.rate_limit import InMemoryRateLimiter, RateLimitExceeded, RedisRateLimiter
from tests.conftest import TEST_DB_URL


@pytest.fixture
def limited(container, engine, clock, outbox):
    """Cliente con límites chicos (la fixture ``container`` limpia la base)."""
    settings = Settings(
        database_url=TEST_DB_URL, event_bus="sync", email_backend="console", jwt_secret="x" * 32,
        rate_limit_storage="memory", login_attempts_per_ip_per_minute=6, login_failures_per_email=3,
        registrations_per_ip_per_hour=2, _env_file=None,
    )
    c = build_container(settings, engine=engine, clock=clock, email_sender=outbox)
    c.register_user.execute(email="ana@test.com", full_name="Ana", password="secreta123", role=Role.ALUMNO)
    c.register_user.execute(email="beto@test.com", full_name="Beto", password="secreta123", role=Role.ALUMNO)
    return TestClient(create_app(c))


def login(client, email="ana@test.com", password="secreta123"):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_registration_is_limited_per_ip(limited):
    for i in range(2):
        r = limited.post("/auth/register", json={"email": f"n{i}@test.com", "full_name": "N", "password": "secreta123"})
        assert r.status_code == 201
    r = limited.post("/auth/register", json={"email": "n9@test.com", "full_name": "N", "password": "secreta123"})
    assert r.status_code == 429
    assert r.json()["error"] == "too_many_requests" and "minutos" in r.json()["detail"]
    assert 3500 < int(r.headers["Retry-After"]) <= 3600


def test_login_failures_lock_only_that_email(limited):
    for _ in range(3):
        assert login(limited, password="incorrecta").status_code == 401
    # Bloqueada aunque ahora la contraseña sea correcta; otra cuenta sigue funcionando.
    locked = login(limited)
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 800
    assert login(limited, email="beto@test.com").status_code == 200


def test_successful_login_resets_failures(limited):
    for _ in range(2):
        login(limited, password="incorrecta")
    assert login(limited).status_code == 200
    for _ in range(2):
        assert login(limited, password="incorrecta").status_code == 401  # el contador volvió a cero


def test_login_attempts_are_limited_per_ip(limited):
    for _ in range(6):
        assert login(limited, email="beto@test.com").status_code == 200
    assert login(limited, email="beto@test.com").status_code == 429


def test_window_expires():
    now = [1000.0]
    limiter = InMemoryRateLimiter(now=lambda: now[0])
    limiter.hit("k", limit=1, window=60)
    with pytest.raises(RateLimitExceeded) as e:
        limiter.hit("k", limit=1, window=60)
    assert e.value.retry_after == 60
    now[0] += 61
    limiter.hit("k", limit=1, window=60)


def test_redis_down_fails_open_and_pauses():
    calls = []

    class DeadPipe:
        def __getattr__(self, name):
            return lambda *a, **k: calls.append(name)

        def execute(self):
            raise RedisConnectionError("redis caído")

    class DeadRedis:
        def pipeline(self):
            return DeadPipe()

    now = [0.0]
    limiter = RedisRateLimiter(DeadRedis(), now=lambda: now[0])
    for _ in range(5):
        limiter.hit("k", limit=1, window=60)  # no lanza: deja pasar
    limiter.check("k", limit=1)
    assert calls.count("incr") == 1  # tras el primer fallo no vuelve a intentar durante la pausa
    now[0] += RedisRateLimiter.PAUSE_AFTER_FAILURE + 1
    limiter.hit("k", limit=1, window=60)
    assert calls.count("incr") == 2


def test_redis_client_does_not_retry():
    limiter = RedisRateLimiter.from_url("redis://127.0.0.1:1/0")  # nadie escucha
    started = time.monotonic()
    limiter.hit("k", limit=1, window=60)
    assert time.monotonic() - started < 1
