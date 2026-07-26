# UNC Transit Delay Prediction Pipeline

Real-time ML pipeline that predicts bus arrival delays for UNC/Chapel Hill Transit using their public GTFS-RT feed. Demonstrates data engineering + ML infrastructure skills across six pipeline stages: ingestion → storage → features → training → serving → dashboard.

## Run & Operate

### Python pipeline (main project)
- `python -m ingestion.mock_server` — start the synthetic GTFS-RT feed server on port 8999 (for local testing)
- `python run_ingestion.py` — start the async polling loop (polls every 15s, writes to SQLite)
- `python -m model.train` — train the Keras model on accumulated data
- `uvicorn serving.app:app --host 0.0.0.0 --port 8000` — start the FastAPI prediction API
- `streamlit run dashboard/app.py` — launch the Streamlit live dashboard
- `pip install -r requirements.txt` — install all Python dependencies

### TypeScript workspace (scaffolding base)
- `pnpm --filter @workspace/api-server run dev` — run the API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas

## Stack

### Python pipeline
- **Ingestion**: `httpx` (async HTTP), `gtfs-realtime-bindings` (protobuf)
- **Storage**: SQLAlchemy + SQLite (default), swappable to Postgres via `DATABASE_URL`
- **Features**: `pandas`, `numpy`
- **Model**: TensorFlow/Keras (small feedforward network)
- **Serving**: FastAPI + uvicorn
- **Dashboard**: Streamlit + Plotly

### TypeScript workspace
- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM

## Where things live

```
/ingestion/poller.py        — async GTFS-RT polling loop
/ingestion/mock_server.py   — synthetic feed server for local testing
/storage/db.py              — SQLAlchemy engine + session factory
/storage/models.py          — ORM models: vehicle_positions, trip_updates
/features/engineering.py    — rolling window aggregation (shared train+serve)
/model/train.py             — Keras model training
/model/artifacts/           — saved model + scaler
/serving/app.py             — FastAPI /predict + /logs endpoints
/dashboard/app.py           — Streamlit live dashboard
requirements.txt            — all Python dependencies
.env.example                — environment variable template
```

## Architecture decisions

- **SQLite by default** — no external service needed; switch to Postgres via `DATABASE_URL` env var only.
- **Shared feature code** — `features/engineering.py` imported by both training and serving; training/serving skew is impossible.
- **Chronological train/eval split** — time-series data; random shuffle would leak future patterns.
- **Lazy model loading** — Keras model loads on first `/predict` call to keep API startup fast.

## User preferences

_Populate as you build._

## Gotchas

- Run the mock server before the poller for local testing; the poller defaults to `localhost:8999`.
- At least a few minutes of polling data are needed before training will succeed.
- After switching `DATABASE_URL` to Postgres, run the poller once to auto-create tables via `init_db()`.

## Pointers

- See `README.md` for full architecture diagram, feature vector spec, and quick start guide.
- See the `pnpm-workspace` skill for TypeScript workspace structure details.
