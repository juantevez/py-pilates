from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pilates.shared.infrastructure.rate_limit import RateLimitExceeded

from pilates.shared.domain.errors import (
    AuthenticationError,
    BusinessRuleViolation,
    ConflictError,
    DomainError,
    NotFoundError,
    PermissionDenied,
)

_STATUS = {
    NotFoundError: 404,
    ConflictError: 409,
    BusinessRuleViolation: 422,
    AuthenticationError: 401,
    PermissionDenied: 403,
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        status = next((code for cls, code in _STATUS.items() if isinstance(exc, cls)), 400)
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        return JSONResponse(status_code=status, content={"error": exc.code, "detail": exc.message}, headers=headers)

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        espera = f"{exc.retry_after} segundos" if exc.retry_after < 60 else f"{-(-exc.retry_after // 60)} minutos"
        return JSONResponse(
            status_code=429,
            content={"error": "too_many_requests", "detail": f"Demasiados intentos. Probá de nuevo en {espera}."},
            headers={"Retry-After": str(exc.retry_after)},
        )
