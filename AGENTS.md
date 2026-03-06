# AGENTS.md

## Project Purpose
Options Chain Analytics Dashboard backend for ingesting FYERS option-chain snapshots into PostgreSQL at configured intervals.

## Architecture Rules (Mandatory)
1. `apps/` contains runnable applications and HTTP route modules.
2. `libs/` contains reusable shared logic (clients, helpers, utilities, DB abstractions, cross-app modules).
3. Service layer must not use DB sessions directly.
4. Service layer must not call raw `session.execute()`.
5. All DB access must go through repository + operations layers.

## Repository Structure Conventions

### Apps
- `apps/fastapi/`
  - FastAPI app only.
  - Route modules live under `apps/fastapi/platform/modules/*/src/route.py`.
  - Keep business orchestration import-only from `libs` and DB operations.
- `apps/scheduler/`
  - Dedicated scheduler app.
  - Start command: `python -m apps.scheduler.src`.
  - Scheduler modules live under `apps/scheduler/platform/modules/*/src/`.

### Libs
- `libs/utils/common/fyers_client/src/`
  - Shared FYERS client implementation.
  - No route concerns.
- `libs/platform/modules/option_chain_snapshot/src/__init__.py`
  - Shared snapshot helper functions used by fastapi + scheduler modules.
- `libs/utils/db/postgres/`
  - `models/src/`: SQLAlchemy models.
  - `src/`: repositories.
  - `operations/src/`: transaction/orchestration layer.

## FYERS Integration Standards
1. Use `fyers-apiv3` SDK for market data (`optionchain`).
2. Token model is daily (`fyers_tokens.token_date`) and no refresh token.
3. Auto-login uses:
   - `FYERS_CLIENT_ID`
   - `FYERS_TOTP_KEY`
   - `FYERS_PIN`
   - `FYERS_APP_ID` / `FYERS_SECRET_KEY`
4. Instrument-to-FYERS mapping must be data-driven via `instruments.fyers_symbol`.
   - Never hardcode symbol mappings in service code.

## Snapshot Engine Standards
1. Ingest only active instruments (`is_active = true`).
2. Capture nearest `SNAPSHOT_EXPIRY_COUNT` expiries.
3. Use `SNAPSHOT_STRIKE_COUNT` directly in FYERS request.
4. Store `captured_at` normalized to interval boundary.
5. Run only during market hours + weekdays.
6. Retry failures using env-configured retry settings.
7. One snapshot write must be one transaction.
   - If strike insert fails, rollback everything.
8. Persist all returned strike rows even when some fields are null.

## Config Checklist (.env)
Required keys:
- `POSTGRES_URI`
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

## Runbook
1. Run migrations:
   - `alembic upgrade head`
2. Seed instruments:
   - `python scripts/seed_instruments.py`
3. Start API:
   - `python -m apps.fastapi.src`
4. Start scheduler app:
   - `python -m apps.scheduler.src`

## Route Surface
- `POST /api/v1/snapshot/trigger` (basic auth)
- `GET /api/v1/snapshot/status` (basic auth)

## Change Management Rules
1. Any new long-running background process must be created as an app in `apps/`.
2. Any reusable external client must be placed under `libs/utils/common/`.
3. Avoid placing non-route cross-app logic under `apps/fastapi`.
4. Keep import boundaries clear: apps depend on libs; libs do not depend on app routes.
5. Before refactors, run import/compile checks:
   - `python -m compileall apps libs scripts`

## Verification After Structural Changes
1. `python -m compileall apps libs scripts`
2. Verify route app imports:
   - `python -m apps.fastapi.src`
3. Verify scheduler app bootstrap:
   - `python -m apps.scheduler.src`
4. Validate migration and snapshot writes on a controlled run.
