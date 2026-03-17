from libs.utils.config.src import config

# Fyers API credentials
FYERS_CLIENT_ID = config["FYERS_CLIENT_ID"]
FYERS_APP_ID = config["FYERS_APP_ID"]
FYERS_SECRET_KEY = config["FYERS_SECRET_KEY"]
FYERS_REDIRECT_URI = config["FYERS_REDIRECT_URI"]
FYERS_LOG_PATH = config.get("FYERS_LOG_PATH", "logs/fyers")

# Snapshot engine configuration
SNAPSHOT_STRIKE_COUNT = int(config.get("SNAPSHOT_STRIKE_COUNT", 13))
SNAPSHOT_EXPIRY_COUNT = int(config.get("SNAPSHOT_EXPIRY_COUNT", 1))
_snapshot_interval_minutes_fallback = int(config.get("SNAPSHOT_INTERVAL_MINUTES", 5))
SNAPSHOT_INTERVAL_SECONDS = int(
    config.get("SNAPSHOT_INTERVAL_SECONDS", _snapshot_interval_minutes_fallback * 60)
)
INSTRUMENTS_SNAPSHOT_INTERVAL_SECONDS = int(
    config.get(
        "INSTRUMENTS_SNAPSHOT_INTERVAL_SECONDS",
        config.get(
            "SNAPSHOT_INTERVAL_SECONDS", _snapshot_interval_minutes_fallback * 60
        ),
    )
)
SCRIPTS_SNAPSHOT_INTERVAL_SECONDS = int(
    config.get("SCRIPTS_SNAPSHOT_INTERVAL_SECONDS", 15)
)
if INSTRUMENTS_SNAPSHOT_INTERVAL_SECONDS <= 0:
    raise ValueError("INSTRUMENTS_SNAPSHOT_INTERVAL_SECONDS must be greater than 0")
if SCRIPTS_SNAPSHOT_INTERVAL_SECONDS <= 0:
    raise ValueError("SCRIPTS_SNAPSHOT_INTERVAL_SECONDS must be greater than 0")
SNAPSHOT_MAX_RETRIES = int(config.get("SNAPSHOT_MAX_RETRIES", 3))
SNAPSHOT_RETRY_BASE_DELAY_SECONDS = int(
    config.get("SNAPSHOT_RETRY_BASE_DELAY_SECONDS", 3)
)
LIVE_DATA_SYMBOL_REFRESH_INTERVAL_SECONDS = int(
    config.get("LIVE_DATA_SYMBOL_REFRESH_INTERVAL_SECONDS", SNAPSHOT_INTERVAL_SECONDS)
)
LIVE_DATA_SUBSCRIPTION_STALE_AFTER_SECONDS = int(
    config.get(
        "LIVE_DATA_SUBSCRIPTION_STALE_AFTER_SECONDS",
        SNAPSHOT_INTERVAL_SECONDS * 2,
    )
)

# Market hours (IST)
MARKET_OPEN_HOUR = int(config.get("MARKET_OPEN_HOUR", 9))
MARKET_OPEN_MINUTE = int(config.get("MARKET_OPEN_MINUTE", 0))
MARKET_CLOSE_HOUR = int(config.get("MARKET_CLOSE_HOUR", 15))
MARKET_CLOSE_MINUTE = int(config.get("MARKET_CLOSE_MINUTE", 30))
