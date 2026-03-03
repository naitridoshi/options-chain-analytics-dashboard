from libs.utils.config.src.fastapi import (
    FASTAPI_APP_HOST,
    FASTAPI_APP_PORT,
    FASTAPI_APP_WORKERS,
)

bind = f"{FASTAPI_APP_HOST}:{FASTAPI_APP_PORT}"
worker_class = "uvicorn.workers.UvicornWorker"
workers = FASTAPI_APP_WORKERS

# Trust forwarded headers from any source (for proper client IP detection)
forwarded_allow_ips = "*"

graceful_timeout = 30  # seconds to finish in-flight requests on restart
timeout = 60  # hard kill if a worker hangs
keepalive = 5  # TCP keep-alive

loglevel = "info"
accesslog = "-"  # stdout
errorlog = "-"  # stderr

max_requests = 1000  # recycle workers to dodge memory leaks
max_requests_jitter = 50
preload_app = False  # lower RAM when many workers
