import asyncio
import os
from datetime import datetime, timezone

import httpx
import pyotp
from fyers_apiv3 import fyersModel

from libs.utils.common.custom_logger.src import CustomLogger
from libs.utils.common.fyers_client.src.helpers import (
    b64_str,
    normalize_client_id_parts,
    parse_auth_code_from_url,
    sha256_hexdigest,
)
from libs.utils.config.src.fyers import (
    FYERS_APP_ID,
    FYERS_CLIENT_ID,
    FYERS_LOG_PATH,
    FYERS_PIN,
    FYERS_REDIRECT_URI,
    FYERS_SECRET_KEY,
    FYERS_TOTP_KEY,
    FYERS_USER_ID,
)
from libs.utils.db.postgres.operations.src import FyersTokenOperations

log = CustomLogger("FyersClientService")
logger, listener = log.get_logger()
listener.start()


class FyersClientService:
    API_BASE = "https://api-t1.fyers.in"
    VAGATOR_BASE = "https://api-t2.fyers.in"

    @classmethod
    async def get_valid_access_token(cls) -> str:
        token_row = await FyersTokenOperations.get_today_token()
        if token_row and token_row.access_token:
            return token_row.access_token
        return await cls.auto_login()

    @classmethod
    async def auto_login(cls) -> str:
        client_app_id, app_type = normalize_client_id_parts(FYERS_CLIENT_ID)
        app_id = FYERS_APP_ID or client_app_id

        async with httpx.AsyncClient(timeout=30) as client:
            send_otp_res = await client.post(
                f"{cls.VAGATOR_BASE}/vagator/v2/send_login_otp_v2",
                json={"fy_id": b64_str(FYERS_USER_ID), "app_id": "2"},
            )
            send_otp_res.raise_for_status()
            send_otp_payload = send_otp_res.json()
            request_key = cls._extract_request_key(send_otp_payload)
            if not request_key:
                raise ValueError(f"FYERS send OTP failed: {send_otp_payload}")

            totp_value = pyotp.TOTP(FYERS_TOTP_KEY).now()
            verify_otp_res = await client.post(
                f"{cls.VAGATOR_BASE}/vagator/v2/verify_otp",
                json={"request_key": request_key, "otp": totp_value},
            )
            verify_otp_res.raise_for_status()
            verify_otp_payload = verify_otp_res.json()
            pin_request_key = cls._extract_request_key(verify_otp_payload)
            if not pin_request_key:
                raise ValueError(f"FYERS verify OTP failed: {verify_otp_payload}")

            verify_pin_res = await client.post(
                f"{cls.VAGATOR_BASE}/vagator/v2/verify_pin_v2",
                json={
                    "request_key": pin_request_key,
                    "identity_type": "pin",
                    "identifier": b64_str(FYERS_PIN),
                },
            )
            verify_pin_res.raise_for_status()
            verify_pin_payload = verify_pin_res.json()
            trade_access_token = cls._extract_access_token(verify_pin_payload)
            if not trade_access_token:
                raise ValueError(f"FYERS verify PIN failed: {verify_pin_payload}")

            token_res = await client.post(
                f"{cls.API_BASE}/api/v3/token",
                headers={"Authorization": f"Bearer {trade_access_token}"},
                json={
                    "fyers_id": FYERS_USER_ID,
                    "app_id": app_id,
                    "redirect_uri": FYERS_REDIRECT_URI,
                    "appType": app_type,
                    "code_challenge": "",
                    "state": "state",
                    "scope": "",
                    "nonce": "",
                    "response_type": "code",
                    "create_cookie": True,
                },
            )
            token_res.raise_for_status()
            token_payload = token_res.json()
            auth_url = token_payload.get("Url") or token_payload.get("url")
            auth_code = parse_auth_code_from_url(auth_url or "")
            if not auth_code:
                raise ValueError(
                    "Unable to extract auth code from FYERS token response"
                )

            app_id_hash = sha256_hexdigest(f"{FYERS_CLIENT_ID}:{FYERS_SECRET_KEY}")
            validate_res = await client.post(
                f"{cls.API_BASE}/api/v3/validate-authcode",
                json={
                    "grant_type": "authorization_code",
                    "appIdHash": app_id_hash,
                    "code": auth_code,
                },
            )
            validate_res.raise_for_status()
            validate_payload = validate_res.json()
            final_access_token = cls._extract_access_token(validate_payload)
            if not final_access_token:
                raise ValueError(f"FYERS validate auth code failed: {validate_payload}")

        await FyersTokenOperations.upsert_today_token(
            access_token=final_access_token,
            expires_at=datetime.now(timezone.utc),
        )
        logger.info("Generated FYERS access token for today")
        return final_access_token

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
            client_id=FYERS_CLIENT_ID,
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

    @staticmethod
    def _extract_request_key(payload: dict) -> str:
        return (
            payload.get("request_key")
            or payload.get("data", {}).get("request_key")
            or payload.get("requestKey")
            or payload.get("data", {}).get("requestKey")
        )

    @staticmethod
    def _extract_access_token(payload: dict) -> str:
        return (
            payload.get("access_token")
            or payload.get("data", {}).get("access_token")
            or payload.get("data", {}).get("accessToken")
        )
