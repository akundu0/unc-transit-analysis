"""
FastAPI model serving layer.

Endpoints:
    GET  /healthz           — liveness probe
    POST /predict           — predict arrival delay for a feature vector
    GET  /logs              — return recent request logs (latency, timestamp)

Start with:
    uvicorn serving.app:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import pickle
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from features.engineering import compute_features, to_vector, FEATURE_NAMES

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

ARTIFACTS_DIR = Path(__file__).parent.parent / "model" / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "delay_model.keras"
SCALER_PATH = ARTIFACTS_DIR / "scaler.pkl"

# In-memory circular buffer of recent request logs (latency, timestamp)
_request_log: deque = deque(maxlen=1000)


# ---------------------------------------------------------------------------
# Model loading (lazy — on first request so startup stays fast)
# ---------------------------------------------------------------------------

_model = None
_scaler = None


def _load_model():
    global _model, _scaler
    if _model is not None:
        return

    if not MODEL_PATH.exists():
        raise FileNotFoundError(
            f"Model artifact not found at {MODEL_PATH}. "
            "Run `python -m model.train` first."
        )
    if not SCALER_PATH.exists():
        raise FileNotFoundError(
            f"Scaler not found at {SCALER_PATH}. "
            "Run `python -m model.train` first."
        )

    import tensorflow as tf  # imported here to keep startup light if TF not available
    _model = tf.keras.models.load_model(str(MODEL_PATH))
    with open(SCALER_PATH, "rb") as f:
        _scaler = pickle.load(f)

    log.info("Model and scaler loaded from %s", ARTIFACTS_DIR)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

app = FastAPI(title="UNC Transit Delay Predictor", version="0.1.0")


class PredictRequest(BaseModel):
    route_id: str
    stop_sequence: Optional[int] = None
    # Optionally pass a pre-computed feature vector (bypasses DB lookup)
    features: Optional[dict] = None


class PredictResponse(BaseModel):
    predicted_delay_seconds: float
    features_used: dict
    latency_ms: float
    timestamp: str


class LogEntry(BaseModel):
    timestamp: str
    latency_ms: float
    route_id: str
    predicted_delay_seconds: float


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest):
    t0 = time.perf_counter()

    try:
        _load_model()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # Use caller-supplied features if provided, otherwise compute from DB
    if req.features:
        features = req.features
    else:
        features = compute_features(route_id=req.route_id, stop_sequence=req.stop_sequence)

    vec = np.array([to_vector(features)], dtype=np.float32)
    vec_scaled = _scaler.transform(vec)
    prediction = float(_model.predict(vec_scaled, verbose=0)[0][0])

    latency_ms = (time.perf_counter() - t0) * 1000
    ts = datetime.now(timezone.utc).isoformat()

    entry = {
        "timestamp": ts,
        "latency_ms": round(latency_ms, 2),
        "route_id": req.route_id,
        "predicted_delay_seconds": round(prediction, 1),
    }
    _request_log.append(entry)
    log.info("predict route=%s delay=%.1fs latency=%.1fms", req.route_id, prediction, latency_ms)

    return PredictResponse(
        predicted_delay_seconds=round(prediction, 1),
        features_used=features,
        latency_ms=round(latency_ms, 2),
        timestamp=ts,
    )


@app.get("/logs", response_model=list[LogEntry])
def get_logs(limit: int = 100):
    """Return the most recent `limit` request log entries."""
    entries = list(_request_log)[-limit:]
    return [LogEntry(**e) for e in entries]
