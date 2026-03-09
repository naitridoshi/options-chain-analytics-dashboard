from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from apps.fastapi.auth.src.basic_auth import verify_basic_auth
from apps.fastapi.platform.modules.market_data.src.service import (
    LiveMarketDataService,
)

market_data_route = APIRouter(prefix="/api/v1/market-data", tags=["Market Data"])


@market_data_route.get("/live")
async def get_live_market_data(
    _: bool = Depends(verify_basic_auth),
    data_type: str = Query(
        "all", regex="^(all|symbols|strikes)$", description="Type of data to return"
    ),
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
        if data_type == "symbols":
            market_data = LiveMarketDataService.get_symbols_only()
            return JSONResponse(
                status_code=200,
                content={"success": True, "data": market_data},
            )
        elif data_type == "strikes":
            market_data = LiveMarketDataService.get_strikes_only()
            return JSONResponse(
                status_code=200,
                content={"success": True, "data": market_data},
            )
        else:  # "all" or default
            market_data = LiveMarketDataService.get_all_market_data()
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
    symbol: str, _: bool = Depends(verify_basic_auth)
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
        market_data = LiveMarketDataService.get_symbol_data(symbol)

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
    strike: str, _: bool = Depends(verify_basic_auth)
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
        market_data = LiveMarketDataService.get_strike_data(strike)

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
