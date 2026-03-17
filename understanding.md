# Runtime Understanding

## Purpose

This project now runs as a Redis-first runtime for the options dashboard.

Primary goals:

- keep HTTP/API concerns in `apps/fastapi`,
- keep periodic option-chain snapshot capture in `apps/scheduler`,
- keep live websocket ingestion in `apps/live_market_data`,
- store current-day dashboard snapshot data in Redis,
- store live `ltp` and `avg_price` in Redis,
- store live underlying spot data in Redis,
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
   - writes heartbeat and runtime status to Redis,
   - keeps streaming work restricted to market hours,
   - keeps housekeeping active after close for finalization and cleanup.

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
- live underlying market data,
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
- `live:underlying:*`
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
2. Only while the market is open, for each active instrument call FYERS option-chain REST.
3. Parse the option-chain response to determine the active option trading symbols for the nearest expiry.
4. Connect to FYERS websocket with those trading symbols.
5. Also subscribe to the instrument underlying symbol itself.
6. Receive `SymbolUpdate` ticks.
7. Write live option-symbol payloads to Redis.
8. Write live underlying spot payloads to Redis.
9. Publish updates on Redis channels for browser websocket consumers.
10. At market close, disconnect the websocket and stop live subscription refresh work.

Important detail:

- `apps/live_market_data` does use FYERS option-chain REST, but only to derive websocket subscription symbols.
- It does not own the dashboard snapshot timeline.
- The live-data app computes underlying `change_from_prev_close` and `change_pct_from_prev_close` using the Redis previous-day final spot snapshot as the base.
- The live-data app checks market state continuously and resumes streaming automatically on the next market open.

### 3. Dashboard Flow

Dashboard flow is now Redis-only for snapshot reads.

Flow:

1. Browser calls `/api/v1/dashboard/data?symbol=...&timeline_limit=...`.
2. FastAPI resolves the instrument from the instrument catalog.
3. FastAPI reads the latest snapshot and timeline directly from Redis.
4. Browser renders strike table and snapshot KPIs from Redis payload.
5. Browser requests websocket ticket from `/api/v1/market-data/ws-ticket`.
6. Browser connects to `/ws/market-data?symbol=...&ticket=...`.
7. Live `ltp`, `avg_price`, `spot`, `change`, and `change %` are patched from websocket updates.

The dashboard no longer falls back to PostgreSQL reads.

Operational consequence:

- if Redis has no intraday snapshot for the current market day, the dashboard returns an empty Redis-shaped payload rather than falling back to PostgreSQL.
- if a current-day snapshot is missing but live underlying data exists in Redis, the dashboard can still seed spot/change values from live data plus the previous-day final snapshot.

## Dashboard Rendering Rules

The dashboard now splits snapshot values and live values intentionally.

Snapshot-driven:

- strike table structure,
- OI / COI aggregates,
- PCR metrics,
- previous-day reference metadata.

Websocket/live Redis-driven:

- option `LTP`,
- option `VWAP` (`avg_price`),
- underlying `spot`,
- underlying `change`,
- underlying `change %`.

Important UI behavior:

- option `LTP` is not rendered from option-chain snapshot payload anymore,
- option `VWAP` is not rendered from snapshot payload,
- spot/change/change% are not tied to the snapshot refresh cadence anymore,
- live values are preserved across table/KPI rerenders until the next websocket tick arrives,
- this avoids blank flashes during dashboard auto-refresh.

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

## Market-Hours Runtime Rules

The market-hours rule is now explicit and enforced.

Scheduler:

- scheduler tick may still fire on its interval,
- but it skips all FYERS option-chain REST capture outside market hours,
- so no snapshot capture happens after market close or before market open.

Live market data:

- streaming startup is skipped when the market is closed,
- streaming loop checks market state every few seconds,
- when the market closes, the FYERS websocket is disconnected,
- live subscription refresh and option-chain symbol discovery stop outside market hours,
- when the market opens again, streaming resumes automatically.

Housekeeping:

- housekeeping remains active after market close,
- this is required for exact-close-preferred previous-day finalization and old trade-date cleanup.

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

Browser auth:

- browser-facing routes now use session-cookie auth,
- users sign in on `/login` with configured app credentials,
- authenticated browser state is stored in a signed session cookie,
- `/dashboard` redirects to `/login` when no session is present.

Browser websocket auth:

- browser websocket upgrade does not rely on basic auth,
- FastAPI issues a short-lived Redis-backed websocket ticket,
- websocket endpoint consumes the ticket before accepting the browser connection.
- websocket ticket consumption is atomic in Redis.

Browser websocket relay:

- FastAPI subscribes to the instrument Redis live channel,
- Redis Pub/Sub messages are streamed directly to the browser websocket,
- relay no longer uses a 1-second polling loop,
- each forwarded payload includes `relay_forwarded_at` for latency inspection.

Live-data degradation behavior:

- repeated FYERS websocket disconnect/error logs are throttled,
- repeated Redis live-write failures are throttled,
- Redis live writes use backoff during outages,
- on Redis recovery, the live-data app resumes normal writes and logs recovery once.

FYERS auth:

- FYERS login is still completed through the login flow,
- browser session auth is required before starting FYERS login,
- live-data and scheduler depend on the daily token being present,
- scheduler snapshot flow remains strict,
- live-data startup is tolerant and retries later.

Operational effect:

- users no longer get browser basic-auth popups for normal dashboard usage,
- logout is handled explicitly through the app,
- browser-facing APIs now authenticate through the active session cookie.

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
- `/login`
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

Auth and session settings:

- `AUTH_USERNAME`
- `AUTH_PASSWORD`
- `AUTH_SESSION_SECRET`
- `AUTH_SESSION_COOKIE_NAME`
- `AUTH_SESSION_MAX_AGE_SECONDS`
- `AUTH_SESSION_SECURE`

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
- browser live spot/change cards will not update,
- previous-day finalization and cleanup will not run.

If market is currently closed:

- scheduler process may still be running but will skip capture work,
- live market data process may still be running but will keep websocket streaming paused,
- housekeeping can still finalize previous-day state and cleanup Redis runtime data.

## Recommended Startup Sequence

1. Start Redis.
2. Run migrations if needed:
   - `alembic upgrade head`
3. Seed instruments if needed:
   - `python scripts/seed_instruments.py`
4. Start FastAPI:
   - `python -m apps.fastapi.src`
5. Open `/login` and sign in with app credentials.
6. Complete FYERS login:
   - `/api/v1/fyers/login`
7. Start scheduler:
   - `python -m apps.scheduler.src`
8. Start live market data app:
   - `python -m apps.live_market_data.src`

Why this order:

- FastAPI must be up before app sign-in and FYERS login can be completed.
- browser session auth must exist before using the FYERS login flow comfortably.
- Scheduler and live-data both depend on the FYERS daily token.
- Starting live-data before login is allowed, but it will wait in degraded mode until token is available.
- Starting scheduler before login will still fail snapshot capture until token exists.

## Verification Checklist

After startup, verify:

1. Browser session login works:
   - `/login`
2. FYERS token exists:
   - `/api/v1/fyers/status`
3. Redis runtime is healthy:
   - `/api/v1/runtime-store/status`
4. Live-data app is healthy:
   - `/api/v1/market-data/status?symbol=NIFTY`
5. Dashboard renders snapshot data:
   - `/dashboard`
6. Scheduler is writing Redis snapshots:
   - dashboard snapshot time should advance on refresh
7. Live websocket is updating prices:
   - dashboard live badges should move to connected
8. Live spot/change values update independently of snapshot refresh:
   - dashboard KPI cards should move on websocket updates even between snapshot intervals
9. Option live cells do not flash blank on refresh:
   - `LTP` and `VWAP` should retain the last websocket-rendered values until the next tick arrives
10. After market close:
   - scheduler logs should show market-closed skips instead of capture
   - live market data logs should show websocket pause/disconnect and no active streaming work

## Latency Verification

To validate live latency end to end:

1. Start:
   - `python -m apps.fastapi.src`
   - `python -m apps.scheduler.src`
   - `python -m apps.live_market_data.src`
2. Sign in on `/login`.
3. Complete FYERS login.
4. Open `/dashboard`.
5. Open browser DevTools and inspect the `/ws/market-data` websocket frames.
6. Compare payload timestamps:
   - `source_received_at`: when `apps/live_market_data` received the FYERS tick
   - `relay_forwarded_at`: when FastAPI forwarded the Redis Pub/Sub payload to the browser

Interpretation:

- if `relay_forwarded_at` is close to `source_received_at`, internal relay latency is low,
- if visible delay still remains high after that, the delay is likely upstream from FYERS tick delivery,
- option `LTP` and especially `VWAP` depend on fresh trade ticks for that symbol and cannot be forced to update every 3-5 seconds if FYERS does not emit them,
- underlying `spot/change/change%` should typically move more frequently than option rows.

## Practical Summary

- `apps/scheduler` owns option-chain REST snapshot capture.
- `apps/live_market_data` owns FYERS websocket ingestion and Redis housekeeping.
- `apps/fastapi` owns serving APIs, dashboard, login, and browser websocket access.
- Dashboard snapshot reads are now Redis-only.
- Live spot/change/change% and option LTP/VWAP are websocket-driven from Redis live data.
- Scheduler capture and live websocket streaming are both restricted to market hours.
- Previous-day close reference in Redis prefers exact close time, else latest snapshot of that day.
- Scheduler and dashboard snapshot runtime are now Redis-native.
- Browser-facing auth now uses signed session cookies instead of browser basic auth.
