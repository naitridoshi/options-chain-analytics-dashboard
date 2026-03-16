import asyncio

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

        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if message and message.get("type") == "message":
                await websocket.send_text(message["data"])
            await asyncio.sleep(0.05)
    except WebSocketDisconnect:
        pass
    finally:
        if pubsub is not None:
            await pubsub.unsubscribe(live_channel_key(instrument_symbol))
            await pubsub.aclose()
