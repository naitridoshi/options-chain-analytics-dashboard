# ✅ UPDATED PHASE 1 PLAN (Repository-Driven Architecture)

---

# Options Chain Snapshot Engine – Phase 1 Implementation Plan (Repository Pattern Enforced)

---

## Project Scope (Phase 1)

Build a production-grade ingestion engine that:

* Tracks **NIFTY** option chain (initially)
* Captures snapshot every **5 minutes**
* Stores normalized strike-level data in PostgreSQL
* Supports addition/removal of instruments
* Handles Fyers token lifecycle
* Uses **Repository + Operations pattern strictly**
* Does NOT allow direct DB access from service layer

No analytics.
No user system.
No strategy engine.

This phase builds the **data ingestion foundation layer only**.

---

# 1. Architectural Enforcement

## 🚨 Mandatory Rule

> Service layer must NEVER access session directly
> Service must NEVER use session.execute()
> Service must NEVER instantiate repositories manually
> All DB access must go through Operations classes

---

## Updated Flow (Correct Pattern)

Scheduler
→ SnapshotService
→ SnapshotOperations
→ Repository Layer
→ Database

Fyers Client remains separate.

---

# 2. Database Schema (Same Tables, Cleaned Constraints)

---

## 2.1 instruments

```sql
id UUID PRIMARY KEY
symbol VARCHAR UNIQUE NOT NULL
exchange VARCHAR NOT NULL
instrument_type VARCHAR NOT NULL
is_active BOOLEAN DEFAULT TRUE
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

---

## 2.2 expiries

```sql
id UUID PRIMARY KEY
instrument_id UUID REFERENCES instruments(id)
expiry_date DATE NOT NULL
is_weekly BOOLEAN DEFAULT TRUE
created_at TIMESTAMPTZ
```

UNIQUE constraint:

```
UNIQUE(instrument_id, expiry_date)
```

---

## 2.3 option_contracts

```sql
id UUID PRIMARY KEY
instrument_id UUID REFERENCES instruments(id)
expiry_id UUID REFERENCES expiries(id)
strike_price NUMERIC NOT NULL
option_type VARCHAR NOT NULL
trading_symbol VARCHAR UNIQUE NOT NULL
lot_size INTEGER
created_at TIMESTAMPTZ
```

UNIQUE constraint:

```
UNIQUE(expiry_id, strike_price, option_type)
```

---

## 2.4 option_chain_snapshots

```sql
id UUID PRIMARY KEY
instrument_id UUID REFERENCES instruments(id)
expiry_id UUID REFERENCES expiries(id)
captured_at TIMESTAMPTZ NOT NULL
spot_price NUMERIC NOT NULL
created_at TIMESTAMPTZ
```

UNIQUE:

```
UNIQUE(instrument_id, expiry_id, captured_at)
```

---

## 2.5 option_chain_strikes

```sql
id UUID PRIMARY KEY
snapshot_id UUID REFERENCES option_chain_snapshots(id)
option_contract_id UUID REFERENCES option_contracts(id)

ltp NUMERIC
volume BIGINT
open_interest BIGINT
oi_change BIGINT
implied_volatility NUMERIC

bid_price NUMERIC
bid_qty BIGINT
ask_price NUMERIC
ask_qty BIGINT

created_at TIMESTAMPTZ
```

---

## 2.6 fyers_tokens

```sql
id UUID PRIMARY KEY
access_token TEXT NOT NULL
refresh_token TEXT
expires_at TIMESTAMPTZ NOT NULL
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

Only 1 active row required.

---

# 3. Folder Structure (Strict Separation)

```
libs/
  utils/db/operations/src/
    instrument_operations.py
    expiry_operations.py
    option_contract_operations.py
    option_snapshot_operations.py
    fyers_token_operations.py

  utils/db/postgres/repositories/src/
    instrument_repository.py
    expiry_repository.py
    option_contract_repository.py
    option_snapshot_repository.py
    fyers_token_repository.py

modules/
  option_chain_snapshot/
    service.py
    dto.py
    scheduler.py
    route.py (optional)
    helpers.py

  fyers_client/
    service.py
    dto.py
    helpers.py
```

---

# 4. Repository Layer

Each repository extends BaseRepository.

Example:


Factory:

```python
def get_instrument_repository(session):
    return BaseRepository(Instrument, session)
```

---

# 5. Operations Layer (Critical Layer)

All DB interactions happen here.

Each operations class:

* Inherits BaseOperations
* Uses postgres_connection.get_session()
* Handles transaction boundaries
* Logs errors
* Returns domain-safe objects

---

## Example Pattern (Must Follow)

```python
class InstrumentOperations(BaseOperations):

    @classmethod
    async def get_active_instruments(cls):
        async with postgres_connection.get_session() as session:
            repo = get_instrument_repository(session)
            return await repo.select_many(
                where_clause=repo.model.is_active.is_(True)
            )
```

Service layer must only call:

```python
await InstrumentOperations.get_active_instruments()
```

Never access session directly.

---

# 6. SnapshotOperations (Core Orchestration Layer)

This is the most important class.

It must:

* Open session
* Resolve instrument
* Resolve expiry (get_or_create)
* Resolve contracts (bulk get/create)
* Create snapshot
* Bulk insert strikes
* Commit transaction

All inside single session block.

---

## Required Methods

```python
class OptionSnapshotOperations(BaseOperations):

    @classmethod
    async def create_snapshot_transactional(
        cls,
        instrument,
        expiry_date,
        snapshot_dto
    )
```

Inside:

```python
async with postgres_connection.get_session() as session:
    # instantiate all repos here
    # perform all operations
    # commit happens automatically
```

---

# 7. Service Layer Responsibilities (Now Clean)

SnapshotService must:

1. Call Fyers client
2. Map raw → DTO
3. Call OptionSnapshotOperations.create_snapshot_transactional()
4. Handle retry logic
5. Log metrics

Service MUST NOT:

* Access session
* Call repository
* Use insert()
* Touch DB directly

---

# 8. Fyers Token Handling

Create:

```python
class FyersTokenOperations(BaseOperations):
```

Responsibilities:

* Get latest token
* Check expiry
* Refresh if needed
* Update DB

Service only calls:

```python
await FyersTokenOperations.get_valid_token()
```

---

# 9. Bulk Insert Rule

Bulk insert logic must live inside repository method:

Example:

```python
class OptionChainStrikeRepository(BaseRepository):

    async def bulk_insert(self, values: list[dict]):
        await self.session.execute(
            insert(self.model),
            values
        )
```

Operations layer calls:

```python
await strike_repo.bulk_insert(values)
```

Service must NEVER call insert().

---

# 10. Scheduler

Scheduler only calls:

```python
await SnapshotService.capture_snapshot(symbol)
```

Scheduler must not use DB.

---

# 11. Error Handling Rules

* All DB exceptions logged inside Operations layer
* Rollback handled automatically by session context
* Service retries Fyers API only
* If DB fails → entire snapshot rolled back

---

# 12. Transaction Boundary Rule

One snapshot = one DB transaction.

If strike insert fails:
→ snapshot row must not exist.

Everything must rollback.

---

# 13. Performance Rules

* Contract resolution must batch query
* Strike insert must bulk insert
* No per-strike DB calls

---

# 14. Logging Requirements

Operations Layer logs:

* Snapshot created
* Contracts auto-created
* Bulk insert count

Service logs:

* API latency
* Retry attempts
* Token refresh

---

# 15. Validation Constraints

* UNIQUE(expiry_id, strike_price, option_type)
* UNIQUE(instrument_id, expiry_id, captured_at)
* UNIQUE(symbol)

Must rely on DB constraints, not manual checks only.

---

# 16. Phase 1 Completion Criteria (Updated)

Phase 1 complete when:

* Scheduler runs every 5 min
* No direct DB calls outside Operations
* All DB logic in repository layer
* Snapshot fully transactional
* Token auto-refresh works
* System runs 1 trading day without failure

---

# 17. Strict Anti-Patterns (Agent Must Avoid)

❌ session.execute in service
❌ Direct insert() in service
❌ Creating repository in route
❌ Multiple transactions per snapshot
❌ Partial snapshot writes

---

# 18. Final Architecture Summary

| Layer        | Responsibility                            |
| ------------ | ----------------------------------------- |
| Service      | Orchestration + API calls                 |
| Operations   | Transaction management + DB orchestration |
| Repository   | Direct DB interaction                     |
| DTO          | Data validation                           |
| Scheduler    | Trigger execution                         |
| Fyers Client | External API                              |

---

# ✅ FINAL RESULT

You now have:

* Clean DDD-like separation
* Strict repository enforcement
* Safe transaction boundaries
* Easily scalable ingestion engine
* Production-grade design

---
