from pathlib import Path

FASTAPI_TEMPLATES_DIR = (
    Path(__file__).parent.parent.parent.parent.parent.parent
    / "apps"
    / "fastapi"
    / "templates"
)


LOGIN_TEMPLATE_HTML = (FASTAPI_TEMPLATES_DIR / "login.html").read_text(encoding="utf-8")
DASHBOARD_TEMPLATE_HTML = (FASTAPI_TEMPLATES_DIR / "dashboard.html").read_text(
    encoding="utf-8"
)
