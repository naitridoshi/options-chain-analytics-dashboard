from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from apps.fastapi.auth.src.basic_auth import verify_basic_auth
from apps.fastapi.src.lifespan import get_app_state
from libs.utils.state.src import AppState

market_data_ws_route = APIRouter(tags=["Market Data WebSocket"])


@market_data_ws_route.websocket("/ws/market-data")
async def websocket_market_data(websocket: WebSocket):
    """WebSocket endpoint for live market data.

    Provides real-time market data updates (LTP, avg_price, volume, OI, bid, ask)
    for subscribed symbols. Data is broadcasted at intervals configured by
    LIVE_DATA_WEBSOCKET_BROADCAST_INTERVAL_MS environment variable.

    Connection Flow:
    1. Client connects to /ws/market-data
    2. Server accepts connection and adds to broadcaster
    3. Server sends initial market state immediately
    4. Server broadcasts updates periodically

    Message Format (JSON):
    {
        "type": "market_data",
        "data": {
            "symbols": {
                "NSE:NIFTY24MAR22000CE": {
                    "symbol": "NSE:NIFTY24MAR22000CE",
                    "ltp": 150.5,
                    "avg_price": 148.2,
                    "volume": 1000,
                    "oi": 5000,
                    "bid": 150.4,
                    "ask": 150.6,
                    "last_update": "2026-03-10T10:30:00.000Z"
                },
                ...
            },
            "strikes": {
                "22000": {
                    "strike": "22000",
                    "CE": {...},
                    "PE": {...}
                },
                ...
            },
            "summary": {
                "symbols": 26,
                "strikes": 13,
                "expiry_date": "2026-03-27"
            }
        }
    }
    """
    try:
        app_state: AppState = get_app_state(websocket.app)
    except RuntimeError:
        await websocket.close(code=1011, reason="Application not initialized")
        return

    broadcaster = app_state.broadcaster
    if not broadcaster:
        await websocket.close(code=1011, reason="Broadcaster not initialized")
        return

    if not broadcaster.is_running:
        await websocket.close(code=1011, reason="Broadcaster not running")
        return

    await broadcaster.connect(websocket)

    try:
        while True:
            # Wait for any client messages (ping/pong, subscription changes, etc.)
            await websocket.receive_text()
            # For now, we just keep the connection alive
            # Future: handle subscription messages from client
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket)
    except Exception:
        broadcaster.disconnect(websocket)


@market_data_ws_route.get("/api/v1/market-data/status")
async def get_market_data_status(
    request: Request,
    _: bool = Depends(verify_basic_auth),
):
    """Get status of the market data WebSocket system.

    Returns:
        dict: {
            "success": true,
            "data": {
                "websocket_connected": bool,
                "broadcaster_running": bool,
                "connected_clients": int,
                "symbols_count": int,
                "strikes_count": int,
                "expiry_date": str | null
            }
        }
    """
    try:
        app_state: AppState = get_app_state(request.app)
    except RuntimeError:
        return {
            "success": False,
            "error": "Application not initialized",
        }

    market_data_manager = app_state.market_data_manager
    broadcaster = app_state.broadcaster
    market_state = app_state.market_state

    return {
        "success": True,
        "data": {
            "websocket_connected": market_data_manager.is_connected
            if market_data_manager
            else False,
            "broadcaster_running": broadcaster.is_running if broadcaster else False,
            "connected_clients": broadcaster.connection_count if broadcaster else 0,
            "symbols_count": market_state.get_symbol_count() if market_state else 0,
            "strikes_count": market_state.get_strike_count() if market_state else 0,
            "expiry_date": market_state.get_state_summary().get("expiry_date")
            if market_state
            else None,
        },
    }
