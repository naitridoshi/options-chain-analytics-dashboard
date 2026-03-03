import base64
import hashlib
from urllib.parse import parse_qs, urlparse


def sha256_hexdigest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def b64_str(raw: str) -> str:
    return base64.b64encode(raw.encode()).decode()


def parse_auth_code_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    auth_code = query.get("auth_code") or query.get("code")
    if not auth_code:
        return None
    return auth_code[0]


def normalize_client_id_parts(client_id: str) -> tuple[str, str]:
    """
    Split FYERS client id like ABCDE12345-100 into (app_id, app_type).
    """
    if "-" not in client_id:
        return client_id, "100"
    app_id, app_type = client_id.split("-", 1)
    return app_id, app_type
