import pytest
from fastapi.testclient import TestClient

from pilates.bootstrap import build_container
from pilates.config import Settings
from pilates.main import create_app
from tests.conftest import TEST_DB_URL

DOC_PATHS = ["/docs", "/redoc", "/openapi.json"]


def _client(engine, clock, outbox, enabled: bool) -> TestClient:
    settings = Settings(
        database_url=TEST_DB_URL, event_bus="sync", email_backend="console", jwt_secret="x" * 32,
        rate_limit_storage="memory", api_docs_enabled=enabled, _env_file=None,
    )
    return TestClient(create_app(build_container(settings, engine=engine, clock=clock, email_sender=outbox)))


@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_habilitados_por_defecto(engine, clock, outbox, path):
    assert _client(engine, clock, outbox, enabled=True).get(path).status_code == 200


@pytest.mark.parametrize("path", DOC_PATHS)
def test_docs_apagados(engine, clock, outbox, path):
    client = _client(engine, clock, outbox, enabled=False)
    assert client.get(path).status_code == 404
    assert client.get("/health").status_code == 200
