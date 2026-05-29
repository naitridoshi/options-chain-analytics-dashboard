import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal

import httpx
from fyers_apiv3 import fyersModel

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src.helpers import sha256_hexdigest
from libs.utils.common.runtime_store.src import RuntimeTokenService
from libs.utils.config.src.fyers import (
    FYERS_APP_ID,
    FYERS_LOG_PATH,
    FYERS_REDIRECT_URI,
    FYERS_SECRET_KEY,
)

log = CustomLogger("FyersClientService")
logger, listener = log.get_logger()
listener.start()


class FyersClientService:
    API_BASE = "https://api-t1.fyers.in"

    @classmethod
    async def get_valid_access_token(cls) -> str:
        token = await RuntimeTokenService.get_today_token()
        if token and token.access_token:
            return token.access_token
        raise ValueError(
            "FYERS token for today is missing. Complete login flow via /api/v1/fyers/login."
        )

    @classmethod
    def get_login_url(cls) -> str:
        session = fyersModel.SessionModel(
            client_id=FYERS_APP_ID,
            secret_key=FYERS_SECRET_KEY,
            redirect_uri=FYERS_REDIRECT_URI,
            response_type="code",
            grant_type="authorization_code",
        )
        return session.generate_authcode()

    @classmethod
    async def exchange_auth_code_and_store(cls, auth_code: str) -> str:
        access_token = await cls._validate_auth_code(auth_code)
        await RuntimeTokenService.upsert_today_token(
            access_token=access_token,
            expires_at=datetime.now(timezone.utc),
        )
        logger.info("FYERS access token stored for today")
        return access_token

    @classmethod
    async def get_today_token_status(cls) -> dict:
        return (await RuntimeTokenService.get_today_token_status()).__dict__

    @classmethod
    async def fetch_option_chain(
        cls,
        *,
        symbol: str,
        strike_count: int,
        timestamp: int | None = None,
    ) -> dict:
        access_token = await cls.get_valid_access_token()
        os.makedirs(FYERS_LOG_PATH, exist_ok=True)

        model = fyersModel.FyersModel(
            client_id=FYERS_APP_ID,
            token=access_token,
            is_async=False,
            log_path=FYERS_LOG_PATH,
        )
        payload = {
            "symbol": symbol,
            "strikecount": strike_count,
        }
        if timestamp:
            payload["timestamp"] = timestamp

        response = await asyncio.to_thread(model.optionchain, payload)
        if not isinstance(response, dict):
            raise ValueError("FYERS optionchain response is not a dictionary")
        if response.get("s") == "error":
            raise ValueError(f"FYERS optionchain failed: {response}")
        return response

    @classmethod
    def _extract_quote_ltp(cls, response: dict) -> Decimal:
        data = response.get("d") or response.get("data") or response
        rows = []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("d") or data.get("data") or data.get("quotes") or [data]

        if not isinstance(rows, list) or not rows:
            raise ValueError(f"Unable to parse FYERS quote response: {response}")

        payload = rows[0]
        values = payload.get("v") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            values = payload if isinstance(payload, dict) else {}

        # Check for error status within the values object
        if isinstance(values, dict) and values.get("s") == "error":
            error_msg = values.get("errmsg", "Unknown error")
            error_code = values.get("code", "N/A")
            raise ValueError(
                f"FYERS quote API error ({error_code}): {error_msg} - {response}"
            )

        ltp = values.get("lp") or values.get("ltp") or values.get("last_price")
        if ltp is None:
            raise ValueError(f"FYERS quote response missing ltp: {response}")
        return Decimal(str(ltp))

    @classmethod
    async def fetch_quote(cls, *, symbol: str) -> Decimal:
        access_token = await cls.get_valid_access_token()
        os.makedirs(FYERS_LOG_PATH, exist_ok=True)
        model = fyersModel.FyersModel(
            client_id=FYERS_APP_ID,
            token=access_token,
            is_async=False,
            log_path=FYERS_LOG_PATH,
        )
        # FYERS quotes API expects format like: NSE:SBIN-EQ
        payload = {"symbols": symbol}
        response = await asyncio.to_thread(model.quotes, payload)
        if not isinstance(response, dict):
            raise ValueError("FYERS quotes response is not a dictionary")
        if response.get("s") == "error":
            raise ValueError(f"FYERS quotes failed: {response}")
        return cls._extract_quote_ltp(response)

    @classmethod
    def _extract_batch_ltps(
        cls, response: dict, requested_symbols: list[str]
    ) -> dict[str, Decimal]:
        """Parse a multi-symbol Fyers quotes response into {fyers_symbol: ltp}."""
        data = response.get("d") or response.get("data") or response
        rows: list[dict] = []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("d") or data.get("data") or data.get("quotes") or [data]

        result: dict[str, Decimal | None] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Skip individual symbol errors within batch (e.g. invalid symbol)
            if row.get("s") == "error":
                continue
            # Symbol is in the "n" field at the top level of each Fyers quote item
            symbol = row.get("n")
            if not symbol:
                continue
            # Values are in the "v" sub-dict
            values = row.get("v")
            if not isinstance(values, dict):
                values = row
            ltp = values.get("lp") or values.get("ltp") or values.get("last_price")
            result[symbol] = Decimal(str(ltp)) if ltp is not None else None

        # Map requested symbols to results
        mapped: dict[str, Decimal] = {}
        for sym in requested_symbols:
            ltp = result.get(sym)
            if ltp is not None:
                mapped[sym] = ltp
        return mapped

    @classmethod
    async def fetch_quotes_batch(cls, *, symbols: list[str]) -> dict[str, Decimal]:
        """Fetch LTP for multiple symbols in a single Fyers quotes API call."""
        if not symbols:
            return {}
        access_token = await cls.get_valid_access_token()
        os.makedirs(FYERS_LOG_PATH, exist_ok=True)
        model = fyersModel.FyersModel(
            client_id=FYERS_APP_ID,
            token=access_token,
            is_async=False,
            log_path=FYERS_LOG_PATH,
        )
        payload = {"symbols": ",".join(symbols)}
        response = await asyncio.to_thread(model.quotes, payload)
        if not isinstance(response, dict):
            raise ValueError("FYERS quotes response is not a dictionary")
        if response.get("s") == "error":
            raise ValueError(f"FYERS quotes failed: {response}")
        return cls._extract_batch_ltps(response, symbols)

    @classmethod
    def _extract_batch_with_prev_close(
        cls, response: dict, requested_symbols: list[str]
    ) -> dict[str, dict[str, Decimal | None]]:
        """Parse Fyers quotes response into {fyers_symbol: {"ltp": Decimal, "prev_close": Decimal | None}}."""
        data = response.get("d") or response.get("data") or response
        rows: list[dict] = []
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("d") or data.get("data") or data.get("quotes") or [data]

        result: dict[str, dict[str, Decimal | None]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("s") == "error":
                continue
            symbol = row.get("n")
            if not symbol:
                continue
            values = row.get("v")
            if not isinstance(values, dict):
                values = row
            ltp = values.get("lp") or values.get("ltp") or values.get("last_price")
            prev_close = values.get("prev_close_price")
            result[symbol] = {
                "ltp": Decimal(str(ltp)) if ltp is not None else None,
                "prev_close": Decimal(str(prev_close))
                if prev_close is not None
                else None,
            }

        mapped: dict[str, dict[str, Decimal | None]] = {}
        for sym in requested_symbols:
            quote = result.get(sym)
            if quote and quote.get("ltp") is not None:
                mapped[sym] = quote
        return mapped

    @classmethod
    async def fetch_quotes_batch_with_prev_close(
        cls, *, symbols: list[str]
    ) -> dict[str, dict[str, Decimal | None]]:
        """Fetch LTP and prev_close_price for multiple symbols in a single Fyers quotes API call."""
        if not symbols:
            return {}
        access_token = await cls.get_valid_access_token()
        os.makedirs(FYERS_LOG_PATH, exist_ok=True)
        model = fyersModel.FyersModel(
            client_id=FYERS_APP_ID,
            token=access_token,
            is_async=False,
            log_path=FYERS_LOG_PATH,
        )
        payload = {"symbols": ",".join(symbols)}
        response = await asyncio.to_thread(model.quotes, payload)
        if not isinstance(response, dict):
            raise ValueError("FYERS quotes response is not a dictionary")
        if response.get("s") == "error":
            raise ValueError(f"FYERS quotes failed: {response}")
        return cls._extract_batch_with_prev_close(response, symbols)

    @classmethod
    async def _validate_auth_code(cls, auth_code: str) -> str:
        app_id_hash = sha256_hexdigest(f"{FYERS_APP_ID}:{FYERS_SECRET_KEY}")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{cls.API_BASE}/api/v3/validate-authcode",
                json={
                    "grant_type": "authorization_code",
                    "appIdHash": app_id_hash,
                    "code": auth_code,
                },
            )

        if response.is_error:
            raise ValueError(
                f"FYERS validate-authcode failed ({response.status_code}): {response.text}"
            )

        payload = response.json()
        access_token = payload.get("access_token") or payload.get("data", {}).get(
            "access_token"
        )
        if not access_token:
            raise ValueError(
                f"FYERS validate-authcode returned no access_token: {payload}"
            )
        return access_token
