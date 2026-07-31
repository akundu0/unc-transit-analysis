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

## Quick start (Docker — recommended)

Docker Compose runs the full stack: Postgres, Alembic migrations, the GTFS-RT poller, the FastAPI serving layer, and the Streamlit dashboard.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) **or** [Colima](https://github.com/abiosoft/colima) (macOS lightweight Docker runtime)
- `docker compose` (bundled with Docker Desktop; also available via `brew install docker-compose`)

### 1. Start the Docker daemon

**Docker Desktop** — open the app; the daemon starts automatically.

**Colima (macOS homebrew)**:

```bash
colima start
```

### 2. Configure environment (optional)

```bash
cp .env.example .env
# Defaults already point to the live Chapel Hill Transit GTFS-RT feed.
# Edit only if you need to override URLs or intervals.
```

### 3. Build images and start all services

```bash
docker compose up --build -d
```

This will:
1. Pull `postgres:16-alpine` and build the app image
2. Run Alembic migrations (`migrate` service exits cleanly once done)
3. Start the GTFS-RT poller (polls every 15 s)
4. Start the FastAPI prediction API on **port 8000**
5. Start the Streamlit dashboard on **port 8501**

### 4. Open the dashboard

```
http://localhost:8501
```

The sidebar shows live row counts. Charts populate after a minute or two of polling.

### 5. View logs

```bash
# All services
docker compose logs -f

# Single service
docker compose logs -f poller
docker compose logs -f dashboard
```

### 6. Stop the stack

```bash
docker compose down
```

To also delete the Postgres volume (wipes all collected data):

```bash
docker compose down -v
```

### 7. Restart after code changes

```bash
docker compose up --build -d
```

---

## Local development (without Docker)

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Defaults use SQLite (unc_transit.db); set DATABASE_URL for Postgres.
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

## Database options

### Option 1: SQLite (default)

Zero-setup, good for local dev. Data lives in `unc_transit.db`.

### Option 2: Supabase Cloud Postgres (recommended)

Persistent cloud database — data survives restarts and is accessible from anywhere.

1. Create a free account at [supabase.com](https://supabase.com)
2. Create a new project (Region: US East)
3. Go to **Settings → Database → Connection string → URI tab**
4. Copy the **Session Mode** connection string
5. Set it in `.env`:

```bash
DATABASE_URL=postgresql+psycopg2://postgres.[PROJECT-REF]:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres
```

6. Run migrations: `python -m alembic upgrade head`
7. Start the poller: `python run_ingestion.py`

**Free tier:** 500 MB storage, 50 connections, daily backups.

To use Supabase with Docker Compose, set `DATABASE_URL` in `.env` to your Supabase connection string, then:

```bash
docker compose up --build -d
```

The local Postgres container will start but remain unused — all services read `DATABASE_URL` from `.env`.

### Option 3: Local Postgres (via Docker Compose)

```bash
docker compose up --build -d    # includes a local Postgres container
```

### Switching databases

Only one env var change is needed — the rest of the codebase is DB-agnostic:

```bash
# .env
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/dbname
```

## Static GTFS data (route/stop enrichment)

Download the Chapel Hill Transit schedule data to enrich the dashboard with route names and official colors:

```bash
python scripts/load_gtfs_static.py
```

This downloads `http://mychtransit.org/gtfs` and produces JSON lookup files in `data/gtfs_static/`. The dashboard automatically loads these if present.

## Real-time feed

The poller connects to Chapel Hill Transit's live GTFS-RT feeds by default:

```
GTFS_RT_VEHICLE_POSITIONS_URL=https://mychtransit.org/gtfs-rt/vehiclepositions
GTFS_RT_TRIP_UPDATES_URL=https://mychtransit.org/gtfs-rt/tripupdates
```

**Operating hours:** ~7 AM – 10 PM ET. Non-zero delays appear during rush hours (7-9 AM, 4-6 PM ET).

## Architectural decisions

- **SQLite over Postgres by default** — eliminates external service dependency for local dev and portfolio demos. Switching to Postgres requires only a `DATABASE_URL` change.
- **Shared feature code path** — `features/engineering.py` is imported by both `model/train.py` and `serving/app.py`. Training/serving skew is architecturally impossible.
- **Chronological train/eval split** — time-series data must not be shuffled; random split would leak future patterns into training.
- **Lazy model loading in serving** — the Keras model loads on the first `/predict` call, keeping startup fast.
- **Circular buffer for request logs** — `deque(maxlen=1000)` in the serving process avoids unbounded memory growth without requiring a separate log store.
