"""
Streamlit dashboard — live predictions vs. actual outcomes + throughput/latency stats.

Run with:
    streamlit run dashboard/app.py

Requires the serving API to be running at SERVING_API_URL (default localhost:8000).
"""

import os
import time
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

SERVING_URL = os.getenv("SERVING_API_URL", "http://localhost:8000")
POLL_INTERVAL_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "15"))

st.set_page_config(
    page_title="UNC Transit Delay Dashboard",
    page_icon="🚌",
    layout="wide",
)

st.title("🚌 UNC Transit — Real-Time Delay Predictor")
st.caption(f"Serving API: `{SERVING_URL}` · Refreshes every {POLL_INTERVAL_SECONDS}s")

# ---------------------------------------------------------------------------
# Session state — accumulate actual-vs-predicted over time
# ---------------------------------------------------------------------------
if "history" not in st.session_state:
    st.session_state.history = []   # list of {timestamp, route_id, predicted, actual}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@st.cache_data(ttl=POLL_INTERVAL_SECONDS)
def fetch_logs(limit: int = 200) -> pd.DataFrame:
    try:
        resp = requests.get(f"{SERVING_URL}/logs", params={"limit": limit}, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    except Exception as exc:
        st.warning(f"Could not reach serving API: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=60)
def fetch_db_actuals() -> pd.DataFrame:
    """Pull recent ground-truth delays directly from the event store."""
    try:
        from storage.db import engine
        from sqlalchemy import text
        sql = text(
            """
            SELECT route_id, predicted_arrival_delay_seconds AS actual_delay, polled_at
            FROM trip_updates
            WHERE polled_at >= :since
              AND predicted_arrival_delay_seconds IS NOT NULL
            ORDER BY polled_at DESC
            LIMIT 500
            """
        )
        with engine.connect() as conn:
            df = pd.read_sql(sql, conn, params={"since": datetime.utcnow() - timedelta(hours=2)})
        df["polled_at"] = pd.to_datetime(df["polled_at"])
        return df
    except Exception as exc:
        st.warning(f"Could not query local DB for actuals: {exc}")
        return pd.DataFrame()


def check_api_health() -> bool:
    try:
        resp = requests.get(f"{SERVING_URL}/healthz", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Sidebar controls
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Controls")
    route_filter = st.text_input("Filter by route ID", placeholder="e.g. 400X")
    auto_refresh = st.toggle("Auto-refresh", value=True)
    latency_window = st.slider("Latency chart window (last N requests)", 10, 200, 50)

    st.divider()
    healthy = check_api_health()
    status_colour = "🟢" if healthy else "🔴"
    st.metric("Serving API", f"{status_colour} {'Online' if healthy else 'Offline'}")


# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------
logs_df = fetch_logs(limit=latency_window)
actuals_df = fetch_db_actuals()

# Apply route filter
if route_filter and not logs_df.empty:
    logs_df = logs_df[logs_df["route_id"].str.contains(route_filter, case=False, na=False)]
if route_filter and not actuals_df.empty:
    actuals_df = actuals_df[actuals_df["route_id"].str.contains(route_filter, case=False, na=False)]

# ── Row 1: key metrics ───────────────────────────────────────────────────────
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total predictions served", len(logs_df) if not logs_df.empty else 0)
with col2:
    avg_latency = logs_df["latency_ms"].mean() if not logs_df.empty else 0
    st.metric("Avg latency (ms)", f"{avg_latency:.1f}")
with col3:
    p95_latency = logs_df["latency_ms"].quantile(0.95) if not logs_df.empty else 0
    st.metric("p95 latency (ms)", f"{p95_latency:.1f}")
with col4:
    avg_pred = logs_df["predicted_delay_seconds"].mean() if not logs_df.empty else 0
    st.metric("Avg predicted delay (s)", f"{avg_pred:.0f}")

st.divider()

# ── Row 2: predictions vs actuals ────────────────────────────────────────────
col_left, col_right = st.columns(2)

with col_left:
    st.subheader("Predicted delays over time")
    if not logs_df.empty:
        fig = px.line(
            logs_df.sort_values("timestamp"),
            x="timestamp",
            y="predicted_delay_seconds",
            color="route_id",
            labels={"predicted_delay_seconds": "Delay (s)", "timestamp": "Time"},
        )
        fig.update_layout(height=320, margin=dict(t=20, b=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No prediction logs yet. Make sure the serving API is running and receiving requests.")

with col_right:
    st.subheader("Actual delays (from event store)")
    if not actuals_df.empty:
        fig2 = px.scatter(
            actuals_df.sort_values("polled_at"),
            x="polled_at",
            y="actual_delay",
            color="route_id",
            opacity=0.6,
            labels={"actual_delay": "Delay (s)", "polled_at": "Time"},
        )
        fig2.update_layout(height=320, margin=dict(t=20, b=20))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No actual delay data yet. Start the ingestion poller to collect data.")

# ── Row 3: latency histogram ─────────────────────────────────────────────────
st.subheader(f"Request latency — last {latency_window} requests")
if not logs_df.empty:
    fig3 = go.Figure()
    fig3.add_trace(go.Histogram(
        x=logs_df["latency_ms"],
        nbinsx=20,
        marker_color="#4F8EF7",
        name="Latency (ms)",
    ))
    fig3.update_layout(
        xaxis_title="Latency (ms)",
        yaxis_title="Count",
        height=280,
        margin=dict(t=20, b=20),
    )
    st.plotly_chart(fig3, use_container_width=True)
else:
    st.info("No latency data yet.")

# ── Row 4: raw log table ─────────────────────────────────────────────────────
with st.expander("Raw prediction log"):
    if not logs_df.empty:
        st.dataframe(logs_df.sort_values("timestamp", ascending=False), use_container_width=True)
    else:
        st.write("Empty.")

# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
if auto_refresh:
    time.sleep(POLL_INTERVAL_SECONDS)
    st.rerun()
