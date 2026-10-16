import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
import jwt

from pilates.identity.application.ports import AccessTokenClaims, PasswordHasher, SecretTokenGenerator, TokenService
from pilates.shared.domain.errors import AuthenticationError


class BcryptPasswordHasher(PasswordHasher):
    def hash(self, raw_password: str) -> str:
        return bcrypt.hashpw(raw_password.encode()[:72], bcrypt.gensalt()).decode()

    def verify(self, raw_password: str, password_hash: str) -> bool:
        try:
            return bcrypt.checkpw(raw_password.encode()[:72], password_hash.encode())
        except ValueError:
            return False


class JwtTokenService(TokenService):
    def __init__(self, secret: str, algorithm: str, expires: timedelta) -> None:
        self._secret, self._algorithm, self._expires = secret, algorithm, expires

    def issue(self, user_id: UUID, role: str) -> str:
        now = datetime.now(UTC)
        payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + self._expires}
        return jwt.encode(payload, self._secret, algorithm=self._algorithm)

    def decode(self, token: str) -> AccessTokenClaims:
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
            return AccessTokenClaims(user_id=UUID(payload["sub"]), role=payload["role"])
        except (jwt.PyJWTError, KeyError, ValueError):
            raise AuthenticationError("Token inválido o expirado", code="invalid_token") from None


class Sha256SecretTokenGenerator(SecretTokenGenerator):
    def generate(self) -> str:
        return secrets.token_urlsafe(32)

    def hash(self, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()
