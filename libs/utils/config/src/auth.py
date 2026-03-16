from libs.utils.config.src import config

AUTH_USERNAME = config.get("AUTH_USERNAME", "admin")
AUTH_PASSWORD = config.get("AUTH_PASSWORD", "supersecretpassword")
AUTH_SESSION_SECRET = config.get(
    "AUTH_SESSION_SECRET",
    f"{AUTH_USERNAME}:{AUTH_PASSWORD}:ocad-session-secret",
)
AUTH_SESSION_COOKIE_NAME = config.get("AUTH_SESSION_COOKIE_NAME", "ocad_session")
AUTH_SESSION_MAX_AGE_SECONDS = int(
    config.get("AUTH_SESSION_MAX_AGE_SECONDS", 60 * 60 * 12)
)
AUTH_SESSION_SECURE = str(config.get("AUTH_SESSION_SECURE", "false")).lower() in {
    "1",
    "true",
    "yes",
    "on",
}
