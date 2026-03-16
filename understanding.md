# Runtime Understanding

## Purpose

This project now runs as a Redis-first runtime for the options dashboard.

Primary goals:

- keep HTTP/API concerns in `apps/fastapi`,
- keep periodic option-chain snapshot capture in `apps/scheduler`,
- keep live websocket ingestion in `apps/live_market_data`,
- store current-day dashboard snapshot data in Redis,
- store live `ltp` and `avg_price` in Redis,
- retain one previous-day final snapshot in Redis for next-day comparison,
- remove PostgreSQL from the scheduler/dashboard runtime path.

## High-Level Architecture

There are three runnable apps involved in the runtime.

1. `apps/fastapi`
   - serves dashboard HTML,
   - serves authenticated REST APIs,
   - serves browser websocket endpoint,
   - issues short-lived websocket tickets,
   - reads dashboard snapshot data from Redis,
   - does not own live market ingestion,
   - does not own periodic snapshot scheduling.

2. `apps/scheduler`
   - owns periodic option-chain snapshot capture,
   - calls FYERS option-chain REST at configured intervals during market hours,
   - writes snapshot data to Redis for dashboard reads,
   - no longer depends on PostgreSQL for runtime snapshot capture.

3. `apps/live_market_data`
   - owns live market runtime,
   - acquires a Redis lock so only one instance is active,
   - calls FYERS option-chain REST only to derive live option symbols,
   - connects to FYERS websocket for `SymbolUpdate` ticks,
   - writes live symbol updates to Redis,
   - finalizes and cleans up Redis runtime data,
   - writes heartbeat and runtime status to Redis.

## Ownership Boundaries

The intended ownership model is:

- option-chain REST snapshots for dashboard timeline: `apps/scheduler`
- FYERS websocket ingestion for live display values: `apps/live_market_data`
- dashboard/API/websocket serving to browsers: `apps/fastapi`

This separation is important.

- If websocket ingestion has auth or reconnect issues, periodic dashboard snapshot capture should still remain isolated in the scheduler.
- If scheduler snapshot capture is delayed, live websocket ingestion can still run independently.
- FastAPI remains a serving layer and does not become the owner of background runtime work.

## Redis Storage Model

Redis code lives under:

- `libs/utils/db/redis/src/`

Redis is used for:

- FYERS daily token storage,
- current-day intraday option-chain snapshots,
- current-day trade-date membership tracking,
- current-day latest snapshot pointer,
- previous-day final snapshot retention,
- live symbol market data,
- browser websocket tickets,
- live app lock,
- live app heartbeat,
- rollover markers.

Important key groups:

- `fyers:token:*`
- `snapshots:*`
- `timelines:*`
- `timeline-dates:*`
- `latest:*`
- `previous-day:final:*`
- `live:symbol:*`
- `live:channel:*`
- `websocket:ticket:*`
- `locks:live-market-data`
- `live-app:status`
- `rollover:*`

## Data Flow

### 1. Snapshot Flow

Snapshot capture is owned by `apps/scheduler`.

Flow:

1. Scheduler wakes on configured interval.
2. It runs only during market hours and weekdays.
3. It calls the shared snapshot service.
4. The snapshot service calls FYERS option-chain REST.
5. The response is parsed into:
   - latest interval metrics,
   - strike table rows,
   - instrument metadata,
   - normalized capture timestamp.
6. Snapshot data is written into Redis intraday timeline storage.
7. Snapshot data is persisted to Redis and used directly by the dashboard runtime.

The Redis intraday payload is the data source for:

- `/api/v1/dashboard/data`
- dashboard initial strike table render
- dashboard snapshot metrics

### 2. Live Market Flow

Live market ingestion is owned by `apps/live_market_data`.

Flow:

1. Load active instruments from `data/instruments.json`.
2. For each active instrument, call FYERS option-chain REST.
3. Parse the option-chain response to determine the active option trading symbols for the nearest expiry.
4. Connect to FYERS websocket with those trading symbols.
5. Receive `SymbolUpdate` ticks.
6. Write live per-symbol payloads to Redis.
7. Publish updates on Redis channels for browser websocket consumers.

Important detail:

- `apps/live_market_data` does use FYERS option-chain REST, but only to derive websocket subscription symbols.
- It does not own the dashboard snapshot timeline.

### 3. Dashboard Flow

Dashboard flow is now Redis-only for snapshot reads.

Flow:

1. Browser calls `/api/v1/dashboard/data?symbol=...&timeline_limit=...`.
2. FastAPI resolves the instrument from the instrument catalog.
3. FastAPI reads the latest snapshot and timeline directly from Redis.
4. Browser renders strike table and snapshot KPIs from Redis payload.
5. Browser requests websocket ticket from `/api/v1/market-data/ws-ticket`.
6. Browser connects to `/ws/market-data?symbol=...&ticket=...`.
7. Live `ltp` and `avg_price` cells are patched from websocket updates.

The dashboard no longer falls back to PostgreSQL reads.

Operational consequence:

- if Redis has no intraday snapshot for the current market day, the dashboard returns an empty Redis-shaped payload rather than falling back to PostgreSQL.

## Previous-Day Final Snapshot Logic

Redis retains one previous-day final snapshot per instrument.

This is used for:

- previous close spot reference,
- change from previous close,
- change percent from previous close.

Selection rule:

1. Prefer the exact snapshot at `MARKET_CLOSE_HOUR:MARKET_CLOSE_MINUTE`.
2. If that exact timestamp is not present, use the latest snapshot available for that trade date.

This finalization is handled by `apps/live_market_data` housekeeping after market close plus configured delay.

The stored Redis payload includes the selection mode:

- `exact_close_time`
- `latest_previous_market_day`

## Redis Runtime Safety

The Redis runtime layer has a few important guarantees:

- intraday snapshot writes update the `latest:*` pointer monotonically,
- an older delayed snapshot cannot overwrite a newer latest snapshot,
- websocket tickets are consumed atomically and are one-time use,
- trade dates are tracked in Redis sets and housekeeping does not rely on keyspace scans for normal trade-date discovery.

## Daily Token Handling

FYERS token is treated as daily auth.

Behavior:

- shared FYERS client token lookup is strict,
- live market app handles missing token as a recoverable runtime condition,
- if live-data app starts before daily login, it stays alive and waits for token availability,
- next refresh cycle reconnects automatically after token is stored.

This avoids crashing the live-data app on server startup before login is completed.

## Instrument Catalog

Runtime instrument resolution for Redis and live-data flows uses:

- `data/instruments.json`

through:

- `libs/utils/common/instrument_catalog/src/service.py`

Used by:

- live symbol subscription refresh,
- runtime dashboard symbol resolution,
- housekeeping finalization and cleanup.

## Rollover and Retention

Retention target:

- keep current-day intraday timeline in Redis,
- keep one previous-day final snapshot in Redis,
- delete older intraday trade dates.

Housekeeping behavior:

- periodically checks if market close finalization delay has elapsed,
- finalizes previous-day close snapshot using exact-close-preferred logic,
- avoids repeated work using Redis markers,
- deletes older intraday timelines after finalization,
- resolves trade dates from Redis-maintained trade-date sets.

## Auth Model

HTTP auth:

- basic auth protects REST routes.

Browser websocket auth:

- browser websocket upgrade does not rely on basic auth,
- FastAPI issues a short-lived Redis-backed websocket ticket,
- websocket endpoint consumes the ticket before accepting the browser connection.

FYERS auth:

- FYERS login is still completed through the login flow,
- live-data and scheduler depend on the daily token being present,
- scheduler snapshot flow remains strict,
- live-data startup is tolerant and retries later.

## Legacy PostgreSQL Status

PostgreSQL is no longer part of the scheduler/dashboard runtime path.

Remaining PostgreSQL code is now treated as legacy or compatibility-oriented logic.

Where PostgreSQL write paths still exist, the repository layer has been hardened to reduce uniqueness-race failures by using conflict-safe insert/upsert behavior for:

- FYERS daily tokens,
- instruments,
- expiries,
- option contracts,
- snapshots.

## Operational Status and Validation

Useful runtime endpoints:

- `/health`
- `/api/v1/runtime-store/status`
- `/api/v1/market-data/status?symbol=NIFTY`
- `/api/v1/fyers/status`

These expose:

- Redis availability,
- live app heartbeat presence,
- live app health evaluation,
- FYERS token status,
- runtime snapshot and websocket readiness.

## Important Configuration

Core FYERS and market settings:

- `FYERS_CLIENT_ID`
- `FYERS_APP_ID`
- `FYERS_SECRET_KEY`
- `FYERS_REDIRECT_URI`
- `FYERS_TOTP_KEY`
- `FYERS_PIN`
- `FYERS_LOG_PATH`
- `SNAPSHOT_INTERVAL_SECONDS`
- `SNAPSHOT_STRIKE_COUNT`
- `SNAPSHOT_EXPIRY_COUNT`
- `SNAPSHOT_MAX_RETRIES`
- `SNAPSHOT_RETRY_BASE_DELAY_SECONDS`
- `MARKET_OPEN_HOUR`
- `MARKET_OPEN_MINUTE`
- `MARKET_CLOSE_HOUR`
- `MARKET_CLOSE_MINUTE`

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

## Required Apps to Start

For the full runtime, these processes are required.

1. Redis
   - required for tokens, snapshots, live data, status, tickets, and locking.

2. `apps/fastapi`
   - required for dashboard UI, login flow, APIs, browser websocket endpoint.

3. `apps/scheduler`
   - required for periodic option-chain REST snapshot capture into Redis.

4. `apps/live_market_data`
   - required for FYERS websocket live updates, finalization, cleanup, and runtime heartbeat.

If only FastAPI is started:

- dashboard will load,
- login route will work,
- but Redis snapshot data may be empty,
- and live market updates will not run.

If scheduler is not started:

- no current-day snapshot timeline will be written to Redis,
- dashboard snapshot table and KPIs will remain empty.

If live market data app is not started:

- live websocket updates will not reach Redis,
- browser live price cells will not update,
- previous-day finalization and cleanup will not run.

## Recommended Startup Sequence

1. Start Redis.
2. Run migrations if needed:
   - `alembic upgrade head`
3. Seed instruments if needed:
   - `python scripts/seed_instruments.py`
4. Start FastAPI:
   - `python -m apps.fastapi.src`
5. Complete FYERS login:
   - `/api/v1/fyers/login`
6. Start scheduler:
   - `python -m apps.scheduler.src`
7. Start live market data app:
   - `python -m apps.live_market_data.src`

Why this order:

- FastAPI must be up before login can be completed.
- Scheduler and live-data both depend on the FYERS daily token.
- Starting live-data before login is allowed, but it will wait in degraded mode until token is available.
- Starting scheduler before login will still fail snapshot capture until token exists.

## Verification Checklist

After startup, verify:

1. FYERS token exists:
   - `/api/v1/fyers/status`
2. Redis runtime is healthy:
   - `/api/v1/runtime-store/status`
3. Live-data app is healthy:
   - `/api/v1/market-data/status?symbol=NIFTY`
4. Dashboard renders snapshot data:
   - `/dashboard`
5. Scheduler is writing Redis snapshots:
   - dashboard snapshot time should advance on refresh
6. Live websocket is updating prices:
   - dashboard live badges should move to connected

## Practical Summary

- `apps/scheduler` owns option-chain REST snapshot capture.
- `apps/live_market_data` owns FYERS websocket ingestion and Redis housekeeping.
- `apps/fastapi` owns serving APIs, dashboard, login, and browser websocket access.
- Dashboard snapshot reads are now Redis-only.
- Previous-day close reference in Redis prefers exact close time, else latest snapshot of that day.
- Scheduler and dashboard snapshot runtime are now Redis-native.
