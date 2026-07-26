# UNC Transit Delay Prediction Pipeline

Real-time ML pipeline that predicts bus arrival delays for UNC/Chapel Hill Transit using their public GTFS-RT feed. A portfolio project demonstrating data engineering + ML infrastructure skills across six discrete stages.

## Architecture

```
GTFS-RT feed (VehiclePositions + TripUpdates)
        │
        ▼ every 15 s
┌─────────────────┐       ┌─────────────────┐
│  ingestion/     │──────▶│  storage/       │  SQLite (default)
│  poller.py      │       │  vehicle_positions│  or Postgres
└─────────────────┘       │  trip_updates   │
                          └────────┬────────┘
                                   │
                          ┌────────▼────────┐
                          │  features/      │  rolling aggregates
                          │  engineering.py │  (shared train+serve)
                          └────┬───────┬────┘
                               │       │
                    ┌──────────▼┐     ┌▼──────────────┐
                    │ model/    │     │ serving/       │
                    │ train.py  │     │ app.py (FastAPI)│
                    └──────────┘     └───────┬────────┘
                                             │
                                    ┌────────▼────────┐
                                    │ dashboard/      │
                                    │ app.py (Streamlit)│
                                    └─────────────────┘
```

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set real GTFS-RT URLs when available; defaults point to mock server
```

### 3. Run the mock server (local end-to-end test)

```bash
python -m ingestion.mock_server
```

Starts a synthetic GTFS-RT feed on port 8999 serving five mock UNC routes.

### 4. Start the ingestion poller

```bash
python run_ingestion.py
```

Polls both feeds every 15 seconds, writes to `unc_transit.db` (SQLite).

### 5. Train a model (after accumulating data)

```bash
python -m model.train
```

Requires at least a few polling cycles of data. Saves the model and scaler to `model/artifacts/`.

### 6. Start the prediction API

```bash
uvicorn serving.app:app --host 0.0.0.0 --port 8000
```

### 7. Launch the dashboard

```bash
streamlit run dashboard/app.py
```

## Module overview

| Module | Purpose |
|---|---|
| `ingestion/poller.py` | Async polling loop — `asyncio.gather` over both GTFS-RT feeds |
| `ingestion/mock_server.py` | Synthetic feed server for local testing (FastAPI on port 8999) |
| `storage/db.py` | SQLAlchemy engine + session factory |
| `storage/models.py` | ORM models: `vehicle_positions`, `trip_updates` |
| `features/engineering.py` | Rolling window aggregation; shared by train + serve |
| `model/train.py` | Keras feedforward model; chronological train/eval split |
| `serving/app.py` | FastAPI `/predict` + `/logs` endpoints |
| `dashboard/app.py` | Streamlit live dashboard |

## Feature vector

| Feature | Description |
|---|---|
| `mean_delay_route_15m` | Mean arrival delay (s) for this route in last 15 min |
| `std_delay_route_15m` | Std deviation of delay for this route in last 15 min |
| `mean_delay_route_60m` | Mean arrival delay (s) for this route in last 60 min |
| `vehicle_count_route_15m` | Distinct vehicles seen on route in last 15 min |
| `hour_of_day` | 0–23 |
| `day_of_week` | 0 (Mon) – 6 (Sun) |
| `stop_sequence_norm` | stop_sequence / 50 |

## Switching to Postgres

Set `DATABASE_URL` in `.env`:

```
DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/unc_transit
```

No other code changes needed — SQLAlchemy abstracts the difference.

## Plugging in the real feed

Once you have the UNC/Chapel Hill Transit GTFS-RT URLs, update `.env`:

```
GTFS_RT_VEHICLE_POSITIONS_URL=<real URL>
GTFS_RT_TRIP_UPDATES_URL=<real URL>
```

Restart the poller — no code changes required.

## Architectural decisions

- **SQLite over Postgres by default** — eliminates external service dependency for local dev and portfolio demos. Switching to Postgres requires only a `DATABASE_URL` change.
- **Shared feature code path** — `features/engineering.py` is imported by both `model/train.py` and `serving/app.py`. Training/serving skew is architecturally impossible.
- **Chronological train/eval split** — time-series data must not be shuffled; random split would leak future patterns into training.
- **Lazy model loading in serving** — the Keras model loads on the first `/predict` call, keeping startup fast.
- **Circular buffer for request logs** — `deque(maxlen=1000)` in the serving process avoids unbounded memory growth without requiring a separate log store.
