from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from apps.fastapi.auth.src.basic_auth import verify_basic_auth
from apps.fastapi.platform.modules.market_data.src.service import (
    LiveMarketDataService,
)
from apps.fastapi.src.lifespan import get_app_state
from libs.utils.state.src import AppState

market_data_route = APIRouter(prefix="/api/v1/market-data", tags=["Market Data"])


def get_market_state_from_request(request: Request):
    """Get market state from request app state."""
    app_state: AppState = get_app_state(request.app)
    return app_state.market_state


@market_data_route.get("/live")
async def get_live_market_data(
    request: Request,
    _: bool = Depends(verify_basic_auth),
    data_type: str = "all",
) -> JSONResponse:
    """Get live market data from WebSocket stream.

    Returns real-time market data (LTP, avg_price, volume, OI, bid, ask) for all
    symbols and strikes without hitting the database.

    Query Parameters:
    - data_type: 'all' (symbols + strikes), 'symbols' (only symbols), or 'strikes' (only strikes)

    Response:
        {
            "success": true,
            "data": {
                "symbols": {...},
                "strikes": {...}
            }
        }

    Note: Data is populated by the ingestion service WebSocket stream.
    Empty if WebSocket is not yet connected or no data received.
    """
    try:
        market_state = get_market_state_from_request(request)

        if data_type == "symbols":
            market_data = LiveMarketDataService.get_symbols_only(market_state)
            return JSONResponse(
                status_code=200,
                content={"success": True, "data": market_data},
            )
        elif data_type == "strikes":
            market_data = LiveMarketDataService.get_strikes_only(market_state)
            return JSONResponse(
                status_code=200,
                content={"success": True, "data": market_data},
            )
        else:  # "all" or default
            market_data = LiveMarketDataService.get_all_market_data(market_state)
            return JSONResponse(
                status_code=200,
                content={"success": True, "data": market_data},
            )

    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(error),
            },
        )


@market_data_route.get("/live/symbol/{symbol}")
async def get_live_symbol_data(
    request: Request,
    symbol: str,
    _: bool = Depends(verify_basic_auth),
) -> JSONResponse:
    """Get live market data for a specific symbol.

    Path Parameters:
    - symbol: Option symbol (e.g., NSE:NIFTY24MAR22000CE)

    Response:
        {
            "success": true,
            "data": {
                "ltp": 12.5,
                "avg_price": 12.4,
                "volume": 1000,
                "oi": 5000,
                "bid": 12.3,
                "ask": 12.6,
                "last_update": "2026-03-09T10:30:00.000Z"
            }
        }

    Note: Returns null if symbol is not currently tracked.
    """
    try:
        market_state = get_market_state_from_request(request)
        market_data = LiveMarketDataService.get_symbol_data(symbol, market_state)

        if market_data is None:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "error": f"Symbol {symbol} not found in live market data",
                    "data": None,
                },
            )

        return JSONResponse(
            status_code=200,
            content={"success": True, "data": market_data},
        )

    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(error)},
        )


@market_data_route.get("/live/strike/{strike}")
async def get_live_strike_data(
    request: Request,
    strike: str,
    _: bool = Depends(verify_basic_auth),
) -> JSONResponse:
    """Get live market data for a specific strike (CE + PE).

    Path Parameters:
    - strike: Strike price (e.g., 22000)

    Response:
        {
            "success": true,
            "data": {
                "strike": "22000",
                "CE": {
                    "symbol": "NSE:NIFTY24MAR22000CE",
                    "ltp": 12.5,
                    "avg_price": 12.4,
                    "volume": 1000,
                    "oi": 5000,
                    "bid": 12.3,
                    "ask": 12.6,
                    "last_update": "2026-03-09T10:30:00.000Z"
                },
                "PE": {
                    "symbol": "NSE:NIFTY24MAR22000PE",
                    "ltp": 8.2,
                    ...
                }
            }
        }

    Note: Returns null if strike is not currently tracked.
    """
    try:
        market_state = get_market_state_from_request(request)
        market_data = LiveMarketDataService.get_strike_data(strike, market_state)

        if market_data is None:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "error": f"Strike {strike} not found in live market data",
                    "data": None,
                },
            )

        return JSONResponse(
            status_code=200,
            content={"success": True, "data": market_data},
        )

    except Exception as error:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(error)},
        )
