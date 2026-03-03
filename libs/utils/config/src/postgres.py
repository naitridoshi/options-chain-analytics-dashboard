from libs.utils.config.src import config

POSTGRES_URI = config["POSTGRES_URI"]
POSTGRES_DATABASE_NAME = config["POSTGRES_DATABASE_NAME"]
POSTGRES_ECHO = str(config.get("POSTGRES_ECHO", "false")).lower() in (
    "true",
    "1",
    "yes",
)
POSTGRES_POOL_SIZE = int(config.get("POSTGRES_POOL_SIZE", 10))
POSTGRES_MAX_OVERFLOW = int(config.get("POSTGRES_MAX_OVERFLOW", 20))
POSTGRES_POOL_PRE_PING = str(config.get("POSTGRES_POOL_PRE_PING", "true")).lower() in (
    "true",
    "1",
    "yes",
)
POSTGRES_POOL_RECYCLE = int(config.get("POSTGRES_POOL_RECYCLE", 1800))
