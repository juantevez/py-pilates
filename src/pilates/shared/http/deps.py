from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from pilates.bootstrap import Container
from pilates.identity.application.dto import UserDTO
from pilates.shared.domain.errors import AuthenticationError, PermissionDenied

_bearer = HTTPBearer(auto_error=False)


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


def current_user(
    c: ContainerDep, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]
) -> UserDTO:
    if creds is None:
        raise AuthenticationError("No autenticado", code="not_authenticated")
    claims = c.token_service.decode(creds.credentials)
    return c.get_current_user.execute(claims.user_id)


def require_admin(user: Annotated[UserDTO, Depends(current_user)]) -> UserDTO:
    if user.role != "admin":
        raise PermissionDenied("Requiere rol administrador")
    return user


CurrentUser = Annotated[UserDTO, Depends(current_user)]
AdminUser = Annotated[UserDTO, Depends(require_admin)]
