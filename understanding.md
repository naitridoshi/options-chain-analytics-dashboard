# Runtime Understanding

## Purpose

This branch introduces a Redis-first runtime architecture for the options dashboard.

Primary goals:

- keep FastAPI separate from FYERS live ingestion,
- use a standalone live-data app for websocket ingestion,
- keep current-day option-chain timeline in Redis,
- retain only the previous trading day final snapshot,
- use websocket `ltp` and `avg_price` as live display values,
- keep REST snapshot `ltp` as fallback on initial dashboard render,
- preserve PostgreSQL as a phase-1 operational fallback.

## High-Level Architecture

There are now three runtime layers:

1. `apps/fastapi`
   - serves the dashboard page,
   - serves authenticated APIs,
   - issues short-lived websocket tickets,
   - reads current-day dashboard state from Redis when available,
   - falls back to PostgreSQL where transitional support still exists,
   - subscribes browser clients to Redis-backed websocket channels.

2. `apps/live_market_data`
   - standalone long-running app,
   - acquires a Redis lock so only one instance owns the live runtime,
   - refreshes option symbols from FYERS option-chain REST,
   - connects to FYERS websocket for live symbol updates,
   - writes live `ltp` / `avg_price` into Redis,
   - runs housekeeping for rollover and cleanup,
   - writes heartbeat/status into Redis.

3. Storage
   - Redis is the primary runtime store.
   - PostgreSQL is retained in phase 1 for fallback and compatibility.

## Redis Storage Model

Redis code now lives under:

- `libs/utils/db/redis/src/`

Key responsibilities:

- token storage,
- intraday option snapshot storage,
- previous-day final snapshot retention,
- live symbol market data,
- websocket tickets,
- runtime lock,
- live app heartbeat,
- rollover markers.

Important key groups:

- `fyers:token:*`
- `snapshots:*`
- `timelines:*`
- `latest:*`
- `previous-day:final:*`
- `live:symbol:*`
- `live:channel:*`
- `websocket:ticket:*`
- `locks:live-market-data`
- `live-app:status`
- `rollover:*`

## Data Flow

### 1. Snapshot flow

FastAPI snapshot trigger/scheduler path still captures option-chain REST data.

For the nearest expiry:

- option-chain REST response is parsed,
- PostgreSQL snapshot write still occurs,
- Redis intraday snapshot payload is also written,
- Redis payload contains:
  - latest aggregate metrics,
  - strike table data,
  - market date,
  - refresh interval,
  - instrument metadata.

This payload is what the dashboard prefers when Redis data exists.

### 2. Live market flow

`apps/live_market_data` performs:

- load active instruments from JSON instrument catalog,
- call FYERS option-chain REST to derive active option symbols,
- connect to FYERS websocket using those symbols,
- receive `SymbolUpdate` ticks,
- publish Redis live payloads per instrument channel,
- store latest live symbol state in Redis.

Live payload includes:

- instrument symbol,
- option symbol,
- strike price,
- option type,
- live `ltp`,
- live `avg_price`,
- last update timestamp,
- stale-after threshold.

### 3. Dashboard flow

Dashboard page behavior:

1. Load base structure from `/api/v1/dashboard/data`.
2. Render strike table using snapshot data.
3. Show snapshot `ltp` values immediately as fallback.
4. Request a websocket ticket from `/api/v1/market-data/ws-ticket`.
5. Connect to `/ws/market-data?symbol=...&ticket=...`.
6. Patch live `ltp` and `avg_price` cells as websocket updates arrive.
7. Mark live status as connected, stale, or disconnected based on tick freshness.

## Instrument Catalog

Runtime instrument lookup for Redis/live paths is no longer dependent on PostgreSQL.

Instrument metadata is now read from:

- `data/instruments.json`

through:

- `libs/utils/common/instrument_catalog/src/service.py`

This is used by:

- live symbol subscription refresh,
- housekeeping cleanup/finalization,
- Redis dashboard resolution.

FastAPI startup still seeds PostgreSQL from the same JSON file for phase-1 compatibility.

## Rollover and Retention

Retention target:

- keep current trading day intraday timeline,
- keep only previous trading day final snapshot,
- delete older intraday timelines.

Housekeeping behavior:

- periodically checks whether market-close finalization time has passed,
- saves the latest snapshot of the trade date as previous-day final snapshot,
- uses Redis markers so finalization and cleanup are not repeated,
- deletes intraday timelines older than the current trade date.

## Auth Model

HTTP auth:

- existing basic auth remains in place for REST endpoints.

Websocket auth:

- browser cannot reliably send basic auth during websocket upgrade,
- so FastAPI now issues a short-lived Redis-backed websocket ticket,
- the websocket endpoint consumes that ticket before accepting the connection.

## Operational Status

Useful status endpoints:

- `/health`
- `/api/v1/runtime-store/status`
- `/api/v1/market-data/status?symbol=NIFTY`

These expose:

- Redis availability,
- live app heartbeat presence,
- live app health evaluation,
- runtime snapshot availability,
- websocket readiness.

The live-data app writes heartbeat status into Redis periodically.

## Remaining Transitional Dependencies

The branch is Redis-first, but not fully Redis-only yet.

Still transitional:

- PostgreSQL snapshot write path remains active,
- FastAPI can still fall back to PostgreSQL dashboard reads when Redis data is absent,
- token writes can still write through to PostgreSQL depending on config.

This is intentional for safer migration.

## Important Configuration

Core Redis runtime settings:

- `REDIS_ENABLED`
- `REDIS_URL`
- `REDIS_KEY_PREFIX`
- `REDIS_RUNTIME_STORE_USE_POSTGRES_FALLBACK`
- `REDIS_RUNTIME_STORE_WRITE_THROUGH_POSTGRES`
- `REDIS_TOKEN_TTL_SECONDS`
- `REDIS_LIVE_DATA_TTL_SECONDS`
- `REDIS_INTRADAY_SNAPSHOT_TTL_SECONDS`
- `REDIS_PREVIOUS_DAY_SNAPSHOT_TTL_SECONDS`
- `REDIS_LOCK_TTL_SECONDS`
- `REDIS_ROLLOVER_CHECK_INTERVAL_SECONDS`
- `REDIS_MARKET_CLOSE_FINALIZE_DELAY_SECONDS`
- `REDIS_WEBSOCKET_TICKET_TTL_SECONDS`
- `REDIS_LIVE_APP_HEARTBEAT_TTL_SECONDS`

Live-data specific timing:

- `LIVE_DATA_SYMBOL_REFRESH_INTERVAL_SECONDS`
- `LIVE_DATA_SUBSCRIPTION_STALE_AFTER_SECONDS`

## How to Run

1. Start Redis.
2. Start FastAPI:
   - `python -m apps.fastapi.src`
3. Complete FYERS login:
   - `/api/v1/fyers/login`
4. Start live-data app:
   - `python -m apps.live_market_data.src`
5. Verify:
   - `/api/v1/runtime-store/status`
   - `/api/v1/market-data/status?symbol=NIFTY`
   - `/dashboard`

## Practical Behavior Summary

- FastAPI is no longer the live ingestion owner.
- Redis is the runtime system of record for current-day dashboard data.
- Live websocket values are display-only and not intended for DB analytics.
- Previous-day final snapshot is retained in Redis for next-day context.
- Current-day timeline exists in Redis and is available for intraday dashboard analytics.
- PostgreSQL remains present as a migration safety layer.
