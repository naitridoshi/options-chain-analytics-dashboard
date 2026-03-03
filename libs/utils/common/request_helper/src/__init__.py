import json as json_module
from datetime import datetime, timezone

import requests

from libs.utils.common.constants.src.request_helper import BASIC_HEADERS
from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.date_time.src import get_execution_time_in_seconds

log = CustomLogger("RequestHelper")
logger, listener = log.get_logger()
listener.start()


class RequestHelper:
    """HTTP request helper with session pooling, retry mechanism, and structured logging."""

    def __init__(self):
        self.session = requests.Session()

    def request(
        self,
        url: str,
        headers: dict = None,
        method: str = "GET",
        payload: dict = None,
        json: dict = None,
        timeout: int = 10,
        max_try: int = 3,
        save_to_json_file: bool = False,
        filename: str = "temp.json",
    ) -> requests.Response:
        """
        Make an HTTP request with retry logic and structured logging.

        Args:
            url: Target URL.
            headers: Request headers. Defaults to BASIC_HEADERS.
            method: HTTP method (GET, POST, PUT, DELETE, etc.).
            payload: Form/body data (passed as `data`).
            json: JSON payload (passed as `json`).
            timeout: Request timeout in seconds.
            max_try: Maximum retry attempts.
            save_to_json_file: Save successful response to a JSON file.
            filename: Filename for saved response.

        Returns:
            requests.Response on success.

        Raises:
            Exception: If all retry attempts fail.
        """
        if headers is None:
            headers = BASIC_HEADERS.copy()

        logger.debug(f"Requesting {method} {url} ...")
        start_time = datetime.now(timezone.utc)

        for try_request in range(1, max_try + 1):
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=payload,
                    json=json,
                    timeout=timeout,
                )

                elapsed = get_execution_time_in_seconds(start_time)

                if response.status_code < 400:
                    # 2xx / 3xx — success
                    logger.debug(
                        f"Try: {try_request}, "
                        f"url: {url}, "
                        f"Status Code: {response.status_code}, "
                        f"Response Length: "
                        f"{len(response.text) / 1024 / 1024:.2f} MB, "
                        f"Time Taken: {elapsed}s."
                    )
                    if save_to_json_file:
                        self._save_response_to_file(response, filename)
                    return response

                elif 400 <= response.status_code < 500:
                    # 4xx — client error, do not retry
                    logger.warning(
                        f"CLIENT ERROR - Try {try_request}: "
                        f"url: {url}, "
                        f"Status Code: {response.status_code}, "
                        f"Time Taken: {elapsed}s."
                    )
                    return response

                else:
                    # 5xx — server error, retry
                    logger.warning(
                        f"SERVER ERROR - Try {try_request}: "
                        f"url: {url}, "
                        f"Status Code: {response.status_code}, "
                        f"Time Taken: {elapsed}s."
                    )

            except requests.exceptions.Timeout:
                elapsed = get_execution_time_in_seconds(start_time)
                logger.error(
                    f"TIMEOUT - Try {try_request}, url: {url}, Time Taken: {elapsed}s."
                )

            except requests.exceptions.ConnectionError:
                elapsed = get_execution_time_in_seconds(start_time)
                logger.error(
                    f"CONNECTION ERROR - Try {try_request}, "
                    f"url: {url}, "
                    f"Time Taken: {elapsed}s."
                )

            except Exception as err:
                elapsed = get_execution_time_in_seconds(start_time)
                logger.error(
                    f"ERROR OCCURRED - Try {try_request}, "
                    f"url: {url}, "
                    f"Time Taken: {elapsed}s, "
                    f"Error: {err}"
                )

            if try_request == max_try:
                raise Exception(f"Request to {url} failed after {max_try} attempt(s).")

        return None

    @staticmethod
    def _save_response_to_file(response: requests.Response, filename: str):
        """Save a JSON response to a file."""
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json_module.dump(response.json(), f, indent=2, ensure_ascii=False)
            logger.debug(f"Response saved to {filename}")
        except Exception as e:
            logger.error(f"Failed to save response to {filename}: {e}")


request_helper = RequestHelper()
