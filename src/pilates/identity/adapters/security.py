import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import bcrypt
import jwt

from pilates.identity.application.ports import (
    AccessTokenClaims,
    IssuedToken,
    PasswordHasher,
    SecretTokenGenerator,
    TokenService,
)
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

    def issue(self, user_id: UUID, role: str, session_id: UUID) -> IssuedToken:
        now = datetime.now(UTC)
        expires_at = now + self._expires
        payload = {"sub": str(user_id), "role": role, "sid": str(session_id), "iat": now, "exp": expires_at}
        return IssuedToken(token=jwt.encode(payload, self._secret, algorithm=self._algorithm), expires_at=expires_at)

    def decode(self, token: str) -> AccessTokenClaims:
        """Los tokens sin ``sid`` (anteriores a las sesiones revocables) se rechazan."""
        try:
            payload = jwt.decode(token, self._secret, algorithms=[self._algorithm])
            return AccessTokenClaims(
                user_id=UUID(payload["sub"]), role=payload["role"], session_id=UUID(payload["sid"])
            )
        except (jwt.PyJWTError, KeyError, ValueError):
            raise AuthenticationError("Token inválido o expirado", code="invalid_token") from None


class Sha256SecretTokenGenerator(SecretTokenGenerator):
    def generate(self) -> str:
        return secrets.token_urlsafe(32)

    def hash(self, raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()
