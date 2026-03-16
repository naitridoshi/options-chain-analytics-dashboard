# options-chain-analytics-dashboard

Redis-first runtime architecture for option-chain dashboards with:
- `apps/fastapi` for authenticated APIs, dashboard rendering, and websocket fanout
- `apps/live_market_data` for FYERS websocket ingestion, Redis writes, and day rollover
- PostgreSQL retained as phase-1 operational fallback for snapshot/token flows

## Runtime Model

- Current-day option-chain snapshot timeline is stored in Redis.
- Live `ltp` and `avg_price` are streamed through FYERS websocket into Redis.
- The dashboard loads structural data from `/api/v1/dashboard/data`.
- The browser then patches live `ltp` and `avg_price` through `/ws/market-data`.
- Only the previous trading day final snapshot is retained in Redis for carry-forward reference.

## Apps

- FastAPI:
  - `python -m apps.fastapi.src`
- Live market data app:
  - `python -m apps.live_market_data.src`
- Snapshot scheduler fallback:
  - `python -m apps.scheduler.src`

## Important Endpoints

- `GET /health`
- `GET /api/v1/runtime-store/status`
- `GET /api/v1/market-data/status?symbol=NIFTY`
- `POST /api/v1/market-data/ws-ticket`
- `GET /api/v1/dashboard/data?symbol=NIFTY`
- `WS /ws/market-data?symbol=NIFTY&ticket=...`

## Required Environment Additions

Alongside the existing FYERS/PostgreSQL settings, configure:

- `REDIS_ENABLED=true`
- `REDIS_URL=redis://127.0.0.1:6379/0`
- `REDIS_KEY_PREFIX=ocad`
- `REDIS_RUNTIME_STORE_USE_POSTGRES_FALLBACK=true`
- `REDIS_RUNTIME_STORE_WRITE_THROUGH_POSTGRES=true`
- `REDIS_TOKEN_TTL_SECONDS`
- `REDIS_LIVE_DATA_TTL_SECONDS`
- `REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS`
- `REDIS_PREVIOUS_DAY_SNAPSHOT_TTL_SECONDS`
- `REDIS_LOCK_TTL_SECONDS`
- `REDIS_ROLLOVER_CHECK_INTERVAL_SECONDS`
- `REDIS_MARKET_CLOSE_FINALIZE_DELAY_SECONDS`
- `REDIS_WEBSOCKET_TICKET_TTL_SECONDS`
- `REDIS_LIVE_APP_HEARTBEAT_TTL_SECONDS`
- `LIVE_DATA_SYMBOL_REFRESH_INTERVAL_SECONDS`
- `LIVE_DATA_SUBSCRIPTION_STALE_AFTER_SECONDS`

## Startup Order

1. Start Redis.
2. Start FastAPI.
3. Complete FYERS login through `/api/v1/fyers/login`.
4. Start `apps.live_market_data`.
5. Verify:
   - `/api/v1/runtime-store/status`
   - `/api/v1/market-data/status?symbol=NIFTY`

## Notes

- REST `ltp` remains the fallback value for first render.
- Websocket `ltp` and `avg_price` are the live display truth.
- Redis persistence should be enabled in production so current-day timeline and previous-day final snapshot survive restarts.
