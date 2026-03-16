from fastapi import Depends, HTTPException, Request, status

from libs.utils.config.src.auth import AUTH_PASSWORD, AUTH_USERNAME


def _credentials_valid(username: str, password: str) -> bool:
    return username == AUTH_USERNAME and password == AUTH_PASSWORD


def get_current_user(request: Request) -> str | None:
    user = request.session.get("auth_user")
    return user if isinstance(user, str) and user else None


def verify_basic_auth(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user


def verify_authenticated_user(user: str = Depends(verify_basic_auth)) -> str:
    return user


def create_authenticated_session(request: Request, username: str) -> None:
    request.session.clear()
    request.session["auth_user"] = username


def clear_authenticated_session(request: Request) -> None:
    request.session.clear()


def authenticate_credentials(username: str, password: str) -> bool:
    return _credentials_valid(username, password)
