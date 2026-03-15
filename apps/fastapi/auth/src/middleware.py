import inspect
from datetime import datetime, timezone
from json import JSONDecodeError, loads
from urllib.parse import parse_qs

from starlette.concurrency import iterate_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette_context import context, plugins
from starlette_context.middleware import RawContextMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.custom_logger.src.helper import extra_details_for_req

log = CustomLogger("AppMiddleware")

logger, listener = log.get_logger()
listener.start()


async def logging_iterator(response, iterator, class_name: str, start_time: datetime):
    response_body = dict()
    async for chunk in iterator:
        try:
            payload = loads(chunk.decode("utf-8"))
            if isinstance(payload, dict) and payload.get("is_final"):
                response_body = payload
        except (JSONDecodeError, UnicodeDecodeError, TypeError):
            pass
        yield chunk
    extra = extra_details_for_req(
        inspect,
        class_name,
        response=response,
        response_body=response_body,
        start_time=start_time,
    )
    logger.info("✅ Streaming request completed successfully: ", extra=extra)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        excluded_paths = ["/"]

        if request.url.path in excluded_paths:
            return await call_next(request)

        start_time = datetime.now(timezone.utc)

        body_text = ""
        try:
            body_bytes = await request.body()
            body_text = body_bytes.decode("utf-8", errors="replace")
            request_body = loads(body_text) if body_text else dict()
        except JSONDecodeError:
            request_body = dict()
            if body_text:
                parsed_form = {
                    key: values[0] if values else ""
                    for key, values in parse_qs(body_text).items()
                }
                request_body.update(parsed_form)

        context["userId"] = request_body.get("user_id")

        extra = extra_details_for_req(
            inspect, __class__.__name__, request, request_body
        )
        logger.info("🚀 Request initiated...", extra=extra)

        try:
            response = await call_next(request)
            if "text/event-stream" in response.headers.get("content-type", ""):
                response.body_iterator = logging_iterator(
                    response=response,
                    iterator=response.body_iterator,
                    class_name=__class__.__name__,
                    start_time=start_time,
                )

            else:
                body_iterator = getattr(response, "body_iterator", None)

                # Safely decode for logging (don’t assume there is a chunk)
                body_text = ""
                if body_iterator is not None:
                    chunks = [chunk async for chunk in body_iterator]
                    response.body_iterator = iterate_in_threadpool(iter(chunks))

                    if chunks:
                        try:
                            body_text = b"".join(chunks).decode(
                                "utf-8", errors="replace"
                            )
                        except Exception:
                            body_text = "<unable to decode response body>"

                extra = extra_details_for_req(
                    inspect,
                    __class__.__name__,
                    response=response,
                    response_body=body_text,
                    start_time=start_time,
                )

                logger.info("✅ Request completed successfully", extra=extra)
        except Exception as error:
            response = JSONResponse(
                status_code=500, content={"success": False, "error": str(error)}
            )

            extra = extra_details_for_req(
                inspect,
                __class__.__name__,
                response=response,
                start_time=start_time,
            )
            logger.info("❌ Request failed...", extra=extra)

        return response


middlewares = [
    Middleware(
        ProxyHeadersMiddleware,
        trusted_hosts="*",  # Trust all hosts to properly extract client IP
    ),
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],  # Allow all HTTP methods (GET, POST, etc.)
        allow_headers=["*"],  # Allow all headers
        expose_headers=["Content-Disposition"],
    ),
    Middleware(
        RawContextMiddleware,
        plugins=[plugins.RequestIdPlugin(force_new_uuid=True)],
    ),
    Middleware(LoggingMiddleware),
]
