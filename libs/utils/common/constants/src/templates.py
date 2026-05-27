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
MARKET_BREADTH_TEMPLATE_HTML = (
    FASTAPI_TEMPLATES_DIR / "market_breadth.html"
).read_text(encoding="utf-8")
HEATMAP_TEMPLATE_HTML = (FASTAPI_TEMPLATES_DIR / "heatmap.html").read_text(
    encoding="utf-8"
)
COI_LIVE_TEMPLATE_HTML = (FASTAPI_TEMPLATES_DIR / "coi_live.html").read_text(
    encoding="utf-8"
)
COI_PCR_LIVE_TEMPLATE_HTML = (FASTAPI_TEMPLATES_DIR / "coi_pcr_live.html").read_text(
    encoding="utf-8"
)
MOST_ACTIVE_TEMPLATE_HTML = (FASTAPI_TEMPLATES_DIR / "most_active.html").read_text(
    encoding="utf-8"
)
INDEX_SCRIPTS_TEMPLATE_HTML = (FASTAPI_TEMPLATES_DIR / "index_scripts.html").read_text(
    encoding="utf-8"
)
SCORING_TEMPLATE_HTML = (FASTAPI_TEMPLATES_DIR / "scoring.html").read_text(
    encoding="utf-8"
)
