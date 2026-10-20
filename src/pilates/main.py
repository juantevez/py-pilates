from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pilates.booking.adapters import http as booking_http
from pilates.bootstrap import Container, build_container
from pilates.config import get_settings
from pilates.identity.adapters import http as identity_http
from pilates.scheduling.adapters import http as scheduling_http
from pilates.shared.http.errors import register_error_handlers


def create_app(container: Container | None = None) -> FastAPI:
    """Factory de la app (uvicorn --factory pilates.main:create_app)."""
    container = container or build_container(get_settings())
    docs = container.settings.api_docs_enabled
    app = FastAPI(
        title=container.settings.app_name,
        version="0.1.0",
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in container.settings.cors_origins.split(",")],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_error_handlers(app)
    for router in (
        identity_http.router,
        identity_http.admin_router,
        booking_http.router,
        booking_http.admin_router,
        scheduling_http.router,
        scheduling_http.student_router,
    ):
        app.include_router(router)

    @app.get("/health", tags=["health"])
    def health() -> dict:
        return {"status": "ok"}

    return app
