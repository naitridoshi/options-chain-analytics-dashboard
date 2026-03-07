import asyncio
import os
from datetime import datetime, timezone

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
