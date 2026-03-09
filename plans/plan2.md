# FYERS WebSocket Integration for Avg Price & LTP

## Options Chain Analytics Dashboard – Hybrid Snapshot Architecture

---

# 1. Objective

Enhance the **Options Chain Snapshot Engine** to capture **Avg Price (ATP/VWAP proxy) and LTP in near real-time** using **FYERS WebSocket Market Data**, while continuing to store **Option Chain snapshots every 5 minutes** using the existing architecture.

The system must:

* Fetch **option chain data every 5 minutes**
* Fetch **avg_price and ltp every few seconds**
* Avoid excessive database writes
* Maintain a **clean ingestion architecture**
* Support **analytics dashboards**

---

# 2. Core Design Principle

Separate the **data types by frequency**.

Two categories of data:

### Slow-changing data (snapshot)

Fetched every **5 minutes**

```id="slowdata"
OI
IV
Strike structure
CE/PE chain layout
```

Source:

```
Fyers OptionChain REST API
```

---

### Fast-changing data (streaming)

Fetched **every few seconds**

```id="fastdata"
LTP
Avg Price
Volume
Bid/Ask
```

Source:

```
Fyers WebSocket Market Data
```

---

# 3. High Level Architecture

```id="archflow"
           FYERS APIs
             │
   ┌─────────┴─────────┐
   │                   │
   ▼                   ▼
OptionChain REST     WebSocket
(5 minute)           (few seconds)
   │                   │
   ▼                   ▼
Chain Snapshot      Market Data Stream
   │                   │
   ▼                   ▼
   └────── Merge Engine ──────┘
                 │
                 ▼
             PostgreSQL
                 │
                 ▼
              Dashboard
```

---

# 4. Data Flow Overview

```id="dataflow"
1 Fetch OptionChain (5 min)
2 Generate option symbols
3 Subscribe to WebSocket
4 Stream LTP + avg_price
5 Update in-memory market state
6 Snapshot every 5 minutes
7 Merge REST + WebSocket data
8 Store in PostgreSQL
```

---

# 5. Why This Architecture Works

Advantages:

### Low database load

Instead of storing every tick:

```id="badflow"
1000+ inserts per minute
```

We store:

```id="goodflow"
1 snapshot every 5 minutes
```

---

### Real-time dashboard values

Dashboard can read **latest market_state directly**.

---

### REST API load remains minimal

OptionChain API called:

```id="restfreq"
once every 5 minutes
```

---

# 6. Symbol Generation

The WebSocket requires **full option symbols**.

Format:

```id="symbolformat"
EXCHANGE:UNDERLYINGYYMONSTRIKEOPTIONTYPE
```

Example:

```id="symbolexample"
NSE:NIFTY24MAR22000CE
```

---

# 7. Symbol Generation Process

From OptionChain response:

```id="optionchainfields"
expiry
strike_price
```

Generate:

```id="symbolcode"
CE_symbol = NSE:NIFTY{expiry}{strike}CE
PE_symbol = NSE:NIFTY{expiry}{strike}PE
```

Example:

```id="symbollist"
NSE:NIFTY24MAR22000CE
NSE:NIFTY24MAR22000PE
NSE:NIFTY24MAR22100CE
NSE:NIFTY24MAR22100PE
```

---

# 8. WebSocket Market Data Integration

Use FYERS SDK:

```python
from fyers_apiv3.FyersWebsocket import data_ws
```

Create WebSocket client module.

File:

```
src/market_data/websocket_client.py
```

Responsibilities:

```
connect websocket
subscribe symbols
receive ticks
update market state
handle reconnects
```

---

# 9. WebSocket Subscription

Example:

```python
symbols = [
"NSE:NIFTY24MAR22000CE",
"NSE:NIFTY24MAR22000PE"
]

socket.subscribe(symbols=symbols, data_type="symbolData")
```

---

# 10. Tick Data Example

WebSocket sends:

```json
{
 "symbol": "NSE:NIFTY24MAR22000CE",
 "ltp": 102.5,
 "avg_price": 101.9,
 "volume": 130000,
 "oi": 450000,
 "bid": 102.4,
 "ask": 102.6
}
```

Important fields:

| Field     | Meaning              |
| --------- | -------------------- |
| symbol    | option instrument    |
| ltp       | last traded price    |
| avg_price | average traded price |
| volume    | traded volume        |
| oi        | open interest        |

---

# 11. Market State (In-Memory Store)

Do **not write WebSocket ticks to DB**.

Instead store in memory.

File:

```
src/state/market_state.py
```

Structure:

```python
market_state = {
 symbol: {
   "ltp": float,
   "avg_price": float,
   "volume": int,
   "oi": int,
   "bid": float,
   "ask": float,
   "last_update": datetime
 }
}
```

Updated every tick.

---

# 12. Fast Update Frequency

WebSocket updates arrive:

```
multiple times per second
```

Dashboard refresh interval recommended:

```
2 – 5 seconds
```

Thus **avg_price + ltp appear real-time**.

---

# 13. Snapshot Engine (5 Minutes)

The snapshot engine continues to run unchanged:

```
every 5 minutes
```

But now merges two sources:

```
REST chain data
+
WebSocket market data
```

---

# 14. Snapshot Merge Logic

Pseudo flow:

```python
for strike in strikes:

 CE_symbol = mapping[strike]["CE"]
 PE_symbol = mapping[strike]["PE"]

 CE_market = market_state.get(CE_symbol)
 PE_market = market_state.get(PE_symbol)

 snapshot = {
   strike,
   CE_ltp,
   CE_avg_price,
   CE_oi,
   PE_ltp,
   PE_avg_price,
   PE_oi
 }

 save_to_db(snapshot)
```

---

# 15. Database Schema Update

Add fields:

```
avg_price
bid_price
ask_price
```

Example schema:

```sql
snapshot_time
symbol
strike
option_type
ltp
avg_price
volume
oi
bid
ask
```

---

# 16. Snapshot Storage Example

```text
timestamp: 2026-03-09 12:30
symbol: NSE:NIFTY24MAR22000CE
ltp: 102.5
avg_price: 101.9
volume: 130000
oi: 450000
```

---

# 17. Symbol Refresh Logic

Option chains change when expiry changes.

Therefore refresh chain daily.

Recommended schedule:

```
08:45 AM IST
```

Steps:

```
fetch option chain
rebuild symbols
restart websocket subscription
```

---

# 18. Failure Handling

WebSocket must support:

### Auto reconnect

If connection drops:

```
reconnect websocket
resubscribe symbols
```

Implement callbacks:

```
on_connect
on_error
on_close
```

---

# 19. Logging

Log events:

```
websocket connected
symbols subscribed
tick received
snapshot saved
reconnect attempts
```

Example:

```
[WS] Connected
[WS] Subscribed 40 symbols
[SNAPSHOT] Stored 80 rows
```

---

# 20. Project Folder Structure

Recommended structure:

```
src/

data_sources/
    optionchain_fetcher.py

symbols/
    option_symbol_generator.py

market_data/
    websocket_client.py

state/
    market_state.py

snapshot/
    snapshot_engine.py

db/
    repository.py
```

---

# 21. Implementation Steps

Step 1

Create **symbol generator**.

Step 2

Implement **WebSocket client**.

Step 3

Create **market_state in-memory store**.

Step 4

Modify **snapshot engine** to merge market_state.

Step 5

Update **database schema**.

Step 6

Add **logging and reconnect logic**.

---

# 22. Resulting System Behaviour

| Data Type    | Source    | Frequency |
| ------------ | --------- | --------- |
| Option Chain | REST      | 5 minutes |
| Avg Price    | WebSocket | seconds   |
| LTP          | WebSocket | seconds   |
| Snapshots    | DB        | 5 minutes |

---

# 23. Dashboard Behaviour

Dashboard reads:

```
latest market_state → real-time values
database snapshots → historical analytics
```

Thus the UI shows:

| Strike | CE LTP | CE Avg Price | CE OI | PE LTP | PE Avg Price | PE OI |

---

# 24. Future Improvements

Possible enhancements:

```
Redis state store
multi-instrument support
tick aggregation
VWAP analytics
PCR analytics
```

---

# 25. Key Takeaway

Correct architecture:

```id="finalflow"
OptionChain REST (5 min)
        +
WebSocket Market Data (seconds)
        ↓
In-Memory Market State
        ↓
Snapshot Engine
        ↓
PostgreSQL
        ↓
Analytics Dashboard
```
