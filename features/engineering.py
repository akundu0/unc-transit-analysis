"""
Feature engineering layer.

This module is the *single source of truth* for feature computation — both
the training script and the serving endpoint import from here so training and
serving can never drift apart.

Feature vector (in order, as a dict → list via `to_vector`):
    mean_delay_route_15m     — mean arrival delay (s) for this route in last 15 min
    std_delay_route_15m      — std deviation of delay for this route in last 15 min
    mean_delay_route_60m     — mean arrival delay (s) for this route in last 60 min
    vehicle_count_route_15m  — number of distinct vehicles seen on this route last 15 min
    hour_of_day              — 0–23
    day_of_week              — 0 (Mon) – 6 (Sun)
    stop_sequence_norm       — stop_sequence / 50 (rough normalisation; 0 if unknown)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from sqlalchemy import text

from storage.db import engine

log = logging.getLogger(__name__)

FEATURE_NAMES = [
    "mean_delay_route_15m",
    "std_delay_route_15m",
    "mean_delay_route_60m",
    "vehicle_count_route_15m",
    "hour_of_day",
    "day_of_week",
    "stop_sequence_norm",
]


def _query_trip_updates(route_id: str, window_minutes: int, as_of: datetime) -> pd.DataFrame:
    """Return TripUpdate rows for a route within a rolling time window."""
    since = as_of - timedelta(minutes=window_minutes)
    sql = text(
        """
        SELECT predicted_arrival_delay_seconds AS delay
        FROM trip_updates
        WHERE route_id = :route_id
          AND polled_at >= :since
          AND polled_at <= :as_of
          AND predicted_arrival_delay_seconds IS NOT NULL
        """
    )
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"route_id": route_id, "since": since, "as_of": as_of})
    return df


def _query_vehicle_count(route_id: str, window_minutes: int, as_of: datetime) -> int:
    """Count distinct vehicles seen on a route within the time window."""
    since = as_of - timedelta(minutes=window_minutes)
    sql = text(
        """
        SELECT COUNT(DISTINCT vehicle_id) AS cnt
        FROM vehicle_positions
        WHERE route_id = :route_id
          AND polled_at >= :since
          AND polled_at <= :as_of
        """
    )
    with engine.connect() as conn:
        result = conn.execute(sql, {"route_id": route_id, "since": since, "as_of": as_of})
        row = result.fetchone()
    return int(row[0]) if row else 0


def compute_features(
    route_id: str,
    stop_sequence: Optional[int] = None,
    as_of: Optional[datetime] = None,
) -> dict:
    """
    Compute the feature vector for a given route at a given moment.

    Parameters
    ----------
    route_id      : GTFS route_id string
    stop_sequence : current stop sequence number (None → treated as unknown)
    as_of         : the reference timestamp (defaults to now in UTC)

    Returns
    -------
    dict mapping FEATURE_NAMES to float values
    """
    if as_of is None:
        as_of = datetime.utcnow()

    df_15 = _query_trip_updates(route_id, 15, as_of)
    df_60 = _query_trip_updates(route_id, 60, as_of)
    vehicle_count = _query_vehicle_count(route_id, 15, as_of)

    mean_15 = float(df_15["delay"].mean()) if not df_15.empty else 0.0
    std_15 = float(df_15["delay"].std(ddof=0)) if len(df_15) > 1 else 0.0
    mean_60 = float(df_60["delay"].mean()) if not df_60.empty else 0.0

    return {
        "mean_delay_route_15m": mean_15,
        "std_delay_route_15m": std_15,
        "mean_delay_route_60m": mean_60,
        "vehicle_count_route_15m": float(vehicle_count),
        "hour_of_day": float(as_of.hour),
        "day_of_week": float(as_of.weekday()),
        "stop_sequence_norm": float(stop_sequence) / 50.0 if stop_sequence is not None else 0.0,
    }


def to_vector(features: dict) -> list[float]:
    """Convert a feature dict to an ordered list compatible with the Keras model."""
    return [features[name] for name in FEATURE_NAMES]


def build_training_dataset(start: Optional[datetime] = None, end: Optional[datetime] = None) -> pd.DataFrame:
    """
    Build a labelled dataset from stored history for offline model training.

    Strategy: for each TripUpdate row that has a non-null delay, compute the
    feature vector *as of that row's polled_at* timestamp and use the recorded
    delay as the label.

    Returns a DataFrame with columns [*FEATURE_NAMES, "label"].
    """
    sql = text(
        """
        SELECT id, route_id, stop_id, predicted_arrival_delay_seconds AS label,
               polled_at, stop_id
        FROM trip_updates
        WHERE predicted_arrival_delay_seconds IS NOT NULL
          AND route_id IS NOT NULL
        ORDER BY polled_at
        """
    )
    with engine.connect() as conn:
        rows = pd.read_sql(sql, conn)

    if start:
        rows = rows[rows["polled_at"] >= start]
    if end:
        rows = rows[rows["polled_at"] <= end]

    if rows.empty:
        log.warning("No labelled rows found in DB — collect more data before training.")
        return pd.DataFrame(columns=FEATURE_NAMES + ["label"])

    records = []
    for _, row in rows.iterrows():
        try:
            feats = compute_features(
                route_id=row["route_id"],
                as_of=row["polled_at"],
            )
            feats["label"] = float(row["label"])
            records.append(feats)
        except Exception as exc:
            log.debug("Skipping row id=%s: %s", row["id"], exc)

    df = pd.DataFrame(records)
    log.info("Training dataset: %d rows, %d features", len(df), len(FEATURE_NAMES))
    return df
