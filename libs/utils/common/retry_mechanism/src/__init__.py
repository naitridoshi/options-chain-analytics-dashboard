import asyncio
import random
import time
from typing import Any, Callable, Optional

from libs.utils.common.custom_logger.src import CustomLogger

log = CustomLogger("RetryMechanism")
logger, listener = log.get_logger()
listener.start()


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(self):
        self.max_retries = 3
        self.base_delay = 2.0  # seconds
        self.max_delay = 30.0  # seconds
        self.jitter_factor = 0.5

    def get_exponential_backoff(self, attempt: int) -> float:
        """
        Calculate exponential backoff with jitter.

        Args:
            attempt: Current attempt number (1-indexed)

        Returns:
            Delay in seconds with exponential backoff and jitter
        """
        # Exponential backoff: base_delay * 2^(attempt-1)
        exponential_delay = self.base_delay * (2 ** (attempt - 1))

        # Cap at max delay
        capped_delay = min(exponential_delay, self.max_delay)

        # Add jitter (random value between 0 and jitter_factor * capped_delay)
        jitter = random.uniform(0, self.jitter_factor * capped_delay)

        return min(capped_delay + jitter, self.max_delay)


def parse_fyers_error_code(error: Exception) -> Optional[int]:
    """
    Parse Fyers error code from exception.

    Args:
        error: The exception that occurred

    Returns:
        The Fyers error code if found, None otherwise
    """
    error_str = str(error)

    # Check for HTTP status codes
    if "429" in error_str:
        return 429
    if "400" in error_str:
        return 400
    if "401" in error_str:
        return 401
    if "403" in error_str:
        return 403
    if "404" in error_str:
        return 404
    if "500" in error_str:
        return 500
    if "502" in error_str:
        return 502
    if "503" in error_str:
        return 503

    # Check for Fyers-specific error codes (e.g., -300, -1300, etc.)
    import re

    fyers_code_match = re.search(r"error \((-?\d+)\)", error_str)
    if fyers_code_match:
        return int(fyers_code_match.group(1))

    # Check for Fyers error code in different format: "code": -300
    fyers_code_match2 = re.search(r"'code': (-?\d+)", error_str)
    if fyers_code_match2:
        return int(fyers_code_match2.group(1))

    return None


def is_client_error(error: Exception, error_code: Optional[int] = None) -> bool:
    """
    Determine if the error is a client error that should not be retried.

    Args:
        error: The exception that occurred
        error_code: Optional pre-parsed error code

    Returns:
        True if it's a client error (should not retry), False otherwise
    """
    error_str = str(error).lower()
    code = error_code or parse_fyers_error_code(error)

    # HTTP client errors (400, 401, 403, 404) - should not retry
    if code in [400, 401, 403, 404]:
        return True

    # Fyers-specific client errors
    # -300: "Please provide a valid symbol"
    # -301: Token expired
    # -100: General error
    # -1300: Invalid token
    if code in [-300, -301, -100, -1300]:
        return True

    # Check for specific error messages that indicate client errors
    if any(
        msg in error_str
        for msg in [
            "please provide a valid symbol",
            "invalid symbol",
            "invalid token",
            "token expired",
            "authentication failed",
            "unauthorized",
            "not found",
        ]
    ):
        return True

    return False


def is_rate_limit_error(error: Exception, error_code: Optional[int] = None) -> bool:
    """
    Determine if the error is a rate limit error.

    Args:
        error: The exception that occurred
        error_code: Optional pre-parsed error code

    Returns:
        True if it's a rate limit error, False otherwise
    """
    error_str = str(error).lower()
    code = error_code or parse_fyers_error_code(error)

    # HTTP rate limit
    if code == 429:
        return True

    # Fyers rate limit code (-1260: Request limit reached)
    if code == -1260:
        return True

    # Check for specific messages
    if any(
        msg in error_str
        for msg in [
            "rate limit",
            "too many requests",
            "request limit reached",
            "request limit",
            "rate limit reached",
        ]
    ):
        return True

    return False


def should_retry_error(
    error: Exception, attempt: int, config: RetryConfig
) -> tuple[bool, float]:
    """
    Determine if the error should be retried and calculate delay.

    Args:
        error: The exception that occurred
        attempt: Current attempt number (1-indexed)
        config: Retry configuration

    Returns:
        tuple of (should_retry, delay)
    """
    error_code = parse_fyers_error_code(error)
    error_str = str(error)

    # Check for client errors - don't retry
    if is_client_error(error, error_code):
        logger.error(f"Client error detected, skipping retry: {error}")
        return (False, 0)

    # Check for rate limit errors - use longer delay
    if is_rate_limit_error(error, error_code):
        delay = config.get_exponential_backoff(attempt)
        # Add extra delay for rate limits (minimum 2 seconds)
        delay = min(max(delay, 2.0), config.max_delay)
        logger.warning(
            f"Rate limit error, retrying in {delay:.2f}s (attempt {attempt}/{config.max_retries}): {error}"
        )
        return (True, delay)

    # Check for server errors or other retryable errors
    # 5xx errors, network errors, timeouts, etc.
    error_str_lower = error_str.lower()
    if any(
        x in error_str_lower
        for x in ["timeout", "connection", "network", "502", "503", "504"]
    ):
        delay = config.get_exponential_backoff(attempt)
        logger.warning(
            f"Retryable error, retrying in {delay:.2f}s (attempt {attempt}/{config.max_retries}): {error}"
        )
        return (True, delay)

    # Default: retry with exponential backoff for unknown errors
    delay = config.get_exponential_backoff(attempt)
    logger.warning(
        f"Error, retrying in {delay:.2f}s (attempt {attempt}/{config.max_retries}): {error}"
    )
    return (True, delay)


def retry_function(func: Callable, *args, retries: int = 3, **kwargs) -> Any:
    """
    Attempts to execute a function with the given arguments, retrying up to a
    specified number of times if an exception is raised.

    :param func: The function to execute.
    :param args: Positional arguments to pass to the function.
    :param retries: The number of times to retry the function if an exception
        is raised.
    :param kwargs: Keyword arguments to pass to the function.
    :return: The result of the function if successful.
    :raises Exception: The last exception raised if all retries fail.
    """
    config = RetryConfig()
    config.max_retries = max(1, retries)

    for attempt in range(1, config.max_retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            should_retry, delay = should_retry_error(e, attempt, config)

            if not should_retry or attempt >= config.max_retries:
                logger.error(
                    f"Failed to execute {func.__name__} after {attempt} attempts: {e}"
                )
                raise e

            time.sleep(delay)

    raise RuntimeError("Unreachable: max_retries must be >= 1")


async def async_retry(
    func: Callable[..., Any],
    *args,
    max_retries: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    **kwargs,
) -> Any:
    """
    Executes an async function with retry logic.

    :param func: The async function to execute.
    :param args: Positional arguments to pass to the function.
    :param max_retries: Maximum number of retry attempts.
    :param base_delay: Base delay for exponential backoff (seconds).
    :param max_delay: maximum delay between retries (seconds).
    :param kwargs: Keyword arguments to pass to the function.
    :return: The result of the function if successful.
    :raises Exception: The last exception raised if all retries fail.
    """
    config = RetryConfig()
    config.max_retries = max(1, max_retries)
    config.base_delay = base_delay
    config.max_delay = max_delay

    for attempt in range(1, config.max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            should_retry, delay = should_retry_error(e, attempt, config)

            if not should_retry or attempt >= config.max_retries:
                logger.error(
                    f"Failed to execute {func.__name__} after {attempt} attempts: {e}"
                )
                raise e

            await asyncio.sleep(delay)

    raise RuntimeError("Unreachable: max_retries must be >= 1")


class AsyncRetryWrapper:
    """
    A wrapper class that adds retry functionality to async methods.

    Usage:
        @AsyncRetryWrapper(max_retries=3, base_delay=2.0, max_delay=30.0)
        async def my_async_function(self, *args, **kwargs):
            # Your code here
            pass
    """

    def __init__(self, max_retries=3, base_delay=2.0, max_delay=30.0):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    def __call__(self, func):
        async def wrapper(*args, **kwargs):
            return await async_retry(
                func,
                *args,
                max_retries=self.max_retries,
                base_delay=self.base_delay,
                max_delay=self.max_delay,
                **kwargs,
            )

        return wrapper
