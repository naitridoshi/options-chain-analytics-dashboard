import asyncio
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

import httpx
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src.helpers import sha256_hexdigest
from libs.utils.config.src.fyers import (
    FYERS_APP_ID,
    FYERS_LOG_PATH,
    FYERS_REDIRECT_URI,
    FYERS_SECRET_KEY,
)
from libs.utils.db.postgres.operations.src import FyersTokenOperations

log = CustomLogger("FyersClientService")
logger, listener = log.get_logger()
listener.start()


class FyersClientService:
    API_BASE = "https://api-t1.fyers.in"

    @classmethod
    async def get_valid_access_token(cls) -> str:
        token_row = await FyersTokenOperations.get_today_token()
        if token_row and token_row.access_token:
            return token_row.access_token
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
        await FyersTokenOperations.upsert_today_token(
            access_token=access_token,
            expires_at=datetime.now(timezone.utc),
        )
        logger.info("FYERS access token stored for today")
        return access_token

    @classmethod
    async def get_today_token_status(cls) -> dict:
        token_row = await FyersTokenOperations.get_today_token()
        if not token_row:
            return {
                "has_token": False,
                "token_date": datetime.now(timezone.utc).date().isoformat(),
                "message": "No token stored for today. Complete /api/v1/fyers/login.",
            }

        return {
            "has_token": True,
            "token_date": token_row.token_date.isoformat(),
            "created_at": token_row.created_at.isoformat()
            if getattr(token_row, "created_at", None)
            else None,
            "updated_at": token_row.updated_at.isoformat()
            if getattr(token_row, "updated_at", None)
            else None,
            "expires_at": token_row.expires_at.isoformat()
            if getattr(token_row, "expires_at", None)
            else None,
            "message": "FYERS token for today is available.",
        }

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

    @classmethod
    def create_websocket_client(cls, access_token: str):
        """Create a FYERS WebSocket client for market data.

        Uses the fyers_apiv3.FyersWebsocket.data_ws module as per FYERS SDK.

        Args:
            access_token: Valid FYERS access token (format: appid:accesstoken)

        Returns:
            data_ws.FyersDataSocket: FYERS WebSocket client instance
        """
        try:
            # The access token needs to be in format "appid:accesstoken"
            full_token = f"{FYERS_APP_ID}:{access_token}"

            client = data_ws.FyersDataSocket(
                access_token=full_token,
                log_path=FYERS_LOG_PATH,
                litemode=False,
                write_to_file=False,
                reconnect=True,
            )
            logger.info("WebSocket client created")
            return client
        except ImportError:
            logger.error("fyers_apiv3.FyersWebsocket.data_ws module not available")
            raise

    @classmethod
    def subscribe_symbols(
        cls,
        ws_client,
        symbols: list[str],
        data_type: str = "SymbolUpdate",
    ) -> None:
        """Subscribe to symbols on WebSocket.

        Args:
            ws_client: FYERS WebSocket client (data_ws.FyersDataSocket)
            symbols: List of symbols to subscribe (max 500)
            data_type: Type of data to subscribe
                - "SymbolUpdate": Real-time symbol updates (ltp, volume, etc.)
                - "DepthData": Market depth data
        """
        try:
            if not symbols:
                logger.warning("Empty symbol list provided for subscription")
                return

            if len(symbols) > 500:
                logger.warning(
                    f"Symbol count {len(symbols)} exceeds FYERS limit of 500. "
                    "Consider chunking subscriptions."
                )

            ws_client.subscribe(symbols=symbols, data_type=data_type)
            logger.info(
                f"WebSocket subscription requested - symbols: {len(symbols)}, data_type: {data_type}"
            )
        except Exception as error:
            logger.error(
                f"Failed to subscribe symbols - error: {str(error)}, count: {len(symbols)}"
            )
            raise

    @classmethod
    def unsubscribe_symbols(cls, ws_client, symbols: list[str]) -> None:
        """Unsubscribe from symbols on WebSocket.

        Args:
            ws_client: FYERS WebSocket client
            symbols: List of symbols to unsubscribe
        """
        try:
            if not symbols:
                logger.warning("Empty symbol list provided for unsubscription")
                return

            ws_client.unsubscribe(symbols=symbols)
            logger.info(f"WebSocket unsubscription requested - symbols: {len(symbols)}")
        except Exception as error:
            logger.error(
                f"Failed to unsubscribe symbols - error: {str(error)}, count: {len(symbols)}"
            )

    @classmethod
    def set_websocket_callbacks(
        cls,
        ws_client,
        on_message: Callable | None = None,
        on_connect: Callable | None = None,
        on_disconnect: Callable | None = None,
        on_error: Callable | None = None,
    ) -> None:
        """Set callbacks for WebSocket events.

        Args:
            ws_client: FYERS WebSocket client
            on_message: Callback for message events
            on_connect: Callback for connect events
            on_disconnect: Callback for disconnect events
            on_error: Callback for error events
        """
        try:
            if on_message:
                ws_client.on_message(on_message)
            if on_connect:
                ws_client.on_connect(on_connect)
            if on_disconnect:
                ws_client.on_disconnect(on_disconnect)
            if on_error:
                ws_client.on_error(on_error)
            logger.info("WebSocket callbacks configured")
        except Exception as error:
            logger.error(f"Failed to set WebSocket callbacks - error: {str(error)}")
            raise
