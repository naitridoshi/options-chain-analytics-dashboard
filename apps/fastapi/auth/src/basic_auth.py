from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from libs.utils.config.src.auth import AUTH_PASSWORD, AUTH_USERNAME

security = HTTPBasic()


def verify_basic_auth(credentials: HTTPBasicCredentials = Depends(security)) -> bool:
    """
    Verify basic authentication credentials against environment variables.

    Args:
        credentials: HTTPBasicCredentials from the request

    Returns:
        bool: True if credentials are valid

    Raises:
        HTTPException: If credentials are invalid
    """
    is_username_correct = credentials.username == AUTH_USERNAME
    is_password_correct = credentials.password == AUTH_PASSWORD

    if not (is_username_correct and is_password_correct):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )

    return True


def get_current_user(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """
    Get the current authenticated user.

    Args:
        credentials: HTTPBasicCredentials from the request

    Returns:
        str: The authenticated username

    Raises:
        HTTPException: If credentials are invalid
    """
    verify_basic_auth(credentials)
    return credentials.username
