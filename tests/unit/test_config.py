import pytest
from pydantic import ValidationError

from pilates.config import Settings


@pytest.fixture(autouse=True)
def _sin_jwt_secret_en_el_entorno(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)


@pytest.mark.parametrize("secret", ["", "change-me", "cambiar-por-un-secreto-largo-y-aleatorio", "x" * 31])
def test_rechaza_jwt_secret_inseguro(secret):
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None, jwt_secret=secret)


def test_exige_jwt_secret():
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(_env_file=None)


def test_acepta_jwt_secret_de_32_bytes():
    assert Settings(_env_file=None, jwt_secret="x" * 32).jwt_secret == "x" * 32


def test_el_error_no_muestra_el_secreto():
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, jwt_secret="secreto-corto")
    assert "secreto-corto" not in str(exc.value)
