from libs.utils.config.src import config

FASTAPI_APP_ENVIRONMENT = config.get("FASTAPI_APP_ENVIRONMENT", "development")

FASTAPI_APP_HOST = config.get("FASTAPI_APP_HOST", "127.0.0.1")
FASTAPI_APP_PORT = int(config.get("FASTAPI_APP_PORT", 5000))

FASTAPI_APP_WORKERS = int(config.get("FASTAPI_APP_WORKERS", 2))
FASTAPI_APP_THREADS = int(config.get("FASTAPI_APP_THREADS", 2))


GUNICORN_CONFIG_PATH = config.get(
    "GUNICORN_CONFIG_PATH", "libs/utils/config/src/gunicorn.py"
)


if FASTAPI_APP_ENVIRONMENT == "production":
    required = {"GUNICORN_CONFIG_PATH": GUNICORN_CONFIG_PATH}
    missing = [name for name, val in required.items() if not val]
    if missing:
        raise ValueError(
            f"FastApi App is in Production Environment but missing required config value(s): {', '.join(missing)}"
        )
