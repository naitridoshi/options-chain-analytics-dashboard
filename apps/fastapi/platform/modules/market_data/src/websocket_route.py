import json
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from libs.utils.common.runtime_store.src import RuntimeWebSocketTicketService
from libs.utils.db.redis.src import live_channel_key, redis_client_manager

market_data_ws_route = APIRouter(tags=["Market Data WebSocket"])


@market_data_ws_route.websocket("/ws/market-data")
async def websocket_market_data(websocket: WebSocket):
    instrument_symbol = (websocket.query_params.get("symbol") or "").strip().upper()
    ticket = (websocket.query_params.get("ticket") or "").strip()
    if not instrument_symbol or not ticket:
        await websocket.close(
            code=1008, reason="Missing symbol or ticket query parameter"
        )
        return

    ticket_payload = await RuntimeWebSocketTicketService.consume_ticket(ticket)
    if not ticket_payload:
        await websocket.close(code=1008, reason="Invalid or expired websocket ticket")
        return
    if (ticket_payload.get("symbol") or "").strip().upper() != instrument_symbol:
        await websocket.close(
            code=1008, reason="Websocket ticket does not match symbol"
        )
        return

    await websocket.accept()
    pubsub = None
    try:
        client = await redis_client_manager.get_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(live_channel_key(instrument_symbol))

        async for message in pubsub.listen():
            if not message or message.get("type") != "message":
                continue

            raw_payload = message.get("data")
            if isinstance(raw_payload, bytes):
                raw_payload = raw_payload.decode()

            relay_forwarded_at = datetime.now(timezone.utc).isoformat()
            try:
                payload = json.loads(raw_payload)
            except (TypeError, json.JSONDecodeError):
                await websocket.send_text(raw_payload)
                continue

            payload["relay_forwarded_at"] = relay_forwarded_at
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    finally:
        if pubsub is not None:
            await pubsub.unsubscribe(live_channel_key(instrument_symbol))
            await pubsub.aclose()
