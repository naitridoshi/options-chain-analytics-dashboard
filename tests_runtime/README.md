# Offline Redis Replay Testing

This directory contains isolated test tooling for validating the Redis-first dashboard and websocket flow after market hours.

It does not modify application runtime code.

## What This Tests

The replay harness validates:

- Redis-backed current-day dashboard snapshots
- current-day intraday timeline reads from Redis
- previous-day final snapshot retention in Redis
- websocket ticket issuance and symbol scoping
- websocket fanout through Redis pub/sub
- dashboard live override behavior for `ltp` and `avg_price`

It does not validate:

- real FYERS websocket connectivity
- real FYERS subscription refresh behavior
- market-hours scheduler behavior

## Script

Use:

```bash
python tests_runtime/offline_redis_replay.py --symbol NIFTY --historical-date 2026-03-12 --clear-today-first --stream-live
```

## What The Script Does

For one instrument and one historical Postgres market date, the script:

1. loads all interval summaries and strike summaries from PostgreSQL
2. remaps those snapshots onto today’s trade date
3. writes them into Redis using the same key model used by the runtime app
4. optionally seeds the retained previous-day final snapshot in Redis
5. optionally publishes synthetic live `ltp` and `avg_price` updates through Redis pub/sub

This allows the FastAPI dashboard and websocket route to behave as if live market data exists for today.

## Recommended End-To-End Flow

### 1. Start Redis

Make sure your configured Redis instance is running.

### 2. Start FastAPI

```bash
python -m apps.fastapi.src
```

### 3. Seed Redis From Historical Postgres Data

Example:

```bash
python tests_runtime/offline_redis_replay.py \
  --symbol NIFTY \
  --historical-date 2026-03-12 \
  --clear-today-first
```

This is enough to test Redis-backed dashboard API responses.

### 4. Verify Dashboard API Now Reads Redis

```bash
curl -u <user>:<pass> "http://127.0.0.1:8000/api/v1/dashboard/data?symbol=NIFTY&timeline_limit=10"
```

Expected:

- response should contain current-day-style data
- data should come from Redis-first runtime state
- latest/timeline payload should be populated

You can also check:

```bash
curl -u <user>:<pass> "http://127.0.0.1:8000/api/v1/market-data/status?symbol=NIFTY"
```

### 5. Test Websocket Fanout With Synthetic Live Updates

Run the replay script with streaming enabled:

```bash
python tests_runtime/offline_redis_replay.py \
  --symbol NIFTY \
  --historical-date 2026-03-12 \
  --clear-today-first \
  --stream-live \
  --publish-cycles 120 \
  --publish-interval-seconds 1
```

### 6. Open The Dashboard

```text
http://127.0.0.1:8000/dashboard
```

Expected:

- table loads from Redis-backed dashboard API
- REST `ltp` appears as fallback
- websocket connects
- `ltp` and `avg_price` cells begin changing from synthetic live publishes
- live badge should move to connected, then stale if publishing stops

## Direct Websocket Verification

### Issue A Ticket

```bash
curl -u <user>:<pass> -X POST "http://127.0.0.1:8000/api/v1/market-data/ws-ticket?symbol=NIFTY"
```

### Connect

With a websocket client such as `wscat`:

```bash
wscat -c "ws://127.0.0.1:8000/ws/market-data?symbol=NIFTY&ticket=<ticket>"
```

When the replay script is running with `--stream-live`, messages should arrive continuously.

## Useful Flags

- `--clear-today-first`
  - removes today’s Redis runtime snapshot state for the selected instrument before seeding

- `--skip-previous-day-final`
  - skips previous-day final snapshot seeding

- `--stream-live`
  - publishes synthetic websocket-style updates

- `--publish-cycles`
  - controls how long the synthetic stream runs

- `--publish-interval-seconds`
  - controls update frequency

- `--max-live-strikes`
  - limits streaming to strikes nearest spot to reduce noise

## Notes

- The script remaps historical timestamps onto today so the runtime dashboard service can read them as current-day Redis data.
- The synthetic live stream uses replay-only symbols like `REPLAY:NIFTY:23000:CE`. The dashboard does not depend on these symbols for row matching; it uses `strike_price` and `option_type`.
- If you want to validate fallback behavior again, clear Redis for the instrument and call the dashboard API. It should fall back to Postgres.
