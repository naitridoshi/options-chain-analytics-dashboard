import json
import os

from fastapi import Depends, HTTPException, Request, status

from libs.utils.config.src.auth import (
    AUTH_DISPLAY_NAME,
    AUTH_PASSWORD,
    AUTH_USERNAME,
)


def _get_credentials_file() -> str:
    return os.getenv("AUTH_CREDENTIALS_FILE", "credentials.json")


def _credentials_valid(username: str, password: str) -> bool:
    # 1. Check default credentials from config
    if username == AUTH_USERNAME and password == AUTH_PASSWORD:
        return True

    # 2. Check credentials from dynamic credentials.json
    credentials_path = _get_credentials_file()
    try:
        if os.path.exists(credentials_path):
            with open(credentials_path, "r") as f:
                creds = json.load(f)
                if isinstance(creds, dict) and username in creds:
                    user_data = creds[username]
                    if isinstance(user_data, dict):
                        return user_data.get("password") == password
                    return user_data == password
    except Exception:
        pass

    return False


def _get_display_name(username: str) -> str:
    credentials_path = _get_credentials_file()
    try:
        if os.path.exists(credentials_path):
            with open(credentials_path, "r") as f:
                creds = json.load(f)
                if isinstance(creds, dict) and username in creds:
                    user_data = creds[username]
                    if isinstance(user_data, dict):
                        return user_data.get("display_name", username)
                    return username
    except Exception:
        pass

    if username == AUTH_USERNAME:
        return AUTH_DISPLAY_NAME or username
    return username


def get_current_user(request: Request) -> str | None:
    user = request.session.get("auth_user")
    return user if isinstance(user, str) and user else None


def get_current_display_user(request: Request) -> str | None:
    display_name = request.session.get("auth_display_name")
    if isinstance(display_name, str) and display_name:
        return display_name
    return get_current_user(request)


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
    request.session["auth_display_name"] = _get_display_name(username)


def clear_authenticated_session(request: Request) -> None:
    request.session.clear()


def authenticate_credentials(username: str, password: str) -> bool:
    return _credentials_valid(username, password)
