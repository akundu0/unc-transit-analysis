"""
Streamlit dashboard — UNC Transit real-time + historical delay analysis.

Run with:
    streamlit run dashboard/app.py

Works standalone by reading directly from the event-store DB.
Optionally shows ML prediction stats when the serving API is running.
"""

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import text

from storage.db import engine

load_dotenv()

SERVING_URL = os.getenv("SERVING_API_URL", "http://localhost:8000")
REFRESH_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "15"))

st.set_page_config(
    page_title="UNC Transit Delay Dashboard",
    page_icon="🚌",
    layout="wide",
)

# ---------------------------------------------------------------------------
# DB query helpers (cached)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=REFRESH_SECONDS)
def query_vehicle_positions(since: datetime, limit: int = 5000) -> pd.DataFrame:
    sql = text("""
        SELECT vehicle_id, trip_id, route_id, lat, lon, bearing, speed,
               current_status, stop_id, stop_sequence, polled_at
        FROM vehicle_positions
        WHERE polled_at >= :since
        ORDER BY polled_at DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"since": since, "limit": limit})
    if not df.empty:
        df["polled_at"] = pd.to_datetime(df["polled_at"])
    return df


@st.cache_data(ttl=REFRESH_SECONDS)
def query_trip_updates(since: datetime, limit: int = 5000) -> pd.DataFrame:
    sql = text("""
        SELECT trip_id, route_id, stop_id, stop_sequence,
               predicted_arrival_delay_seconds AS delay_s,
               predicted_departure_delay_seconds AS dep_delay_s,
               arrival_time, polled_at
        FROM trip_updates
        WHERE polled_at >= :since
          AND predicted_arrival_delay_seconds IS NOT NULL
        ORDER BY polled_at DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"since": since, "limit": limit})
    if not df.empty:
        df["polled_at"] = pd.to_datetime(df["polled_at"])
    return df


@st.cache_data(ttl=REFRESH_SECONDS)
def query_ingestion_health() -> dict:
    """Return row counts and latest poll timestamps for health monitoring."""
    with engine.connect() as conn:
        vp_count = conn.execute(text("SELECT COUNT(*) FROM vehicle_positions")).scalar()
        tu_count = conn.execute(text("SELECT COUNT(*) FROM trip_updates")).scalar()
        vp_latest = conn.execute(text("SELECT MAX(polled_at) FROM vehicle_positions")).scalar()
        tu_latest = conn.execute(text("SELECT MAX(polled_at) FROM trip_updates")).scalar()
        vp_routes = conn.execute(text("SELECT COUNT(DISTINCT route_id) FROM vehicle_positions")).scalar()
        tu_routes = conn.execute(text("SELECT COUNT(DISTINCT route_id) FROM trip_updates")).scalar()
    return {
        "vp_count": vp_count or 0,
        "tu_count": tu_count or 0,
        "vp_latest": vp_latest,
        "tu_latest": tu_latest,
        "vp_routes": vp_routes or 0,
        "tu_routes": tu_routes or 0,
    }


@st.cache_data(ttl=60)
def query_route_summary(since: datetime) -> pd.DataFrame:
    sql = text("""
        SELECT route_id,
               COUNT(*)                                       AS obs,
               ROUND(AVG(predicted_arrival_delay_seconds), 1) AS mean_delay,
               MIN(predicted_arrival_delay_seconds)           AS min_delay,
               MAX(predicted_arrival_delay_seconds)           AS max_delay,
               MIN(polled_at) AS first_seen,
               MAX(polled_at) AS last_seen
        FROM trip_updates
        WHERE polled_at >= :since
          AND predicted_arrival_delay_seconds IS NOT NULL
          AND route_id IS NOT NULL
        GROUP BY route_id
        ORDER BY mean_delay DESC
    """)
    with engine.connect() as conn:
        return pd.read_sql(sql, conn, params={"since": since})


@st.cache_data(ttl=REFRESH_SECONDS)
def query_delay_timeseries(since: datetime) -> pd.DataFrame:
    """Average delay per route per 5-minute bucket for trend charts."""
    sql = text("""
        SELECT route_id, predicted_arrival_delay_seconds AS delay, polled_at
        FROM trip_updates
        WHERE polled_at >= :since
          AND predicted_arrival_delay_seconds IS NOT NULL
          AND route_id IS NOT NULL
        ORDER BY polled_at
    """)
    with engine.connect() as conn:
        df = pd.read_sql(sql, conn, params={"since": since})
    if df.empty:
        return df
    df["polled_at"] = pd.to_datetime(df["polled_at"])
    df["time"] = df["polled_at"].dt.floor("5min")
    agg = df.groupby(["route_id", "time"]).agg(
        mean_delay=("delay", "mean"), obs=("delay", "count")
    ).reset_index()
    return agg


def check_api_health() -> bool:
    try:
        return requests.get(f"{SERVING_URL}/healthz", timeout=2).status_code == 200
    except Exception:
        return False


@st.cache_data(ttl=REFRESH_SECONDS)
def fetch_serving_logs(limit: int = 200) -> pd.DataFrame:
    try:
        resp = requests.get(f"{SERVING_URL}/logs", params={"limit": limit}, timeout=3)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("🚌 UNC Transit — Delay Analysis Dashboard")

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("Controls")
    lookback_hours = st.select_slider(
        "Time window",
        options=[1, 2, 4, 8, 12, 24, 48, 72, 168],
        value=4,
        format_func=lambda h: f"{h}h" if h < 24 else f"{h // 24}d",
    )
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    route_filter = st.text_input("Filter by route ID", placeholder="e.g. NS, FCX")
    auto_refresh = st.toggle("Auto-refresh", value=True)

    st.divider()
    st.subheader("System health")
    health = query_ingestion_health()
    st.metric("Vehicle position rows", f"{health['vp_count']:,}")
    st.metric("Trip update rows", f"{health['tu_count']:,}")
    st.metric("Routes tracked (VP/TU)", f"{health['vp_routes']} / {health['tu_routes']}")

    if health["vp_latest"]:
        st.caption(f"Last VP poll: {health['vp_latest']}")
    if health["tu_latest"]:
        st.caption(f"Last TU poll: {health['tu_latest']}")

    api_online = check_api_health()
    st.metric("Serving API", "🟢 Online" if api_online else "🔴 Offline")


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
vp_df = query_vehicle_positions(since)
tu_df = query_trip_updates(since)
ts_df = query_delay_timeseries(since)
route_df = query_route_summary(since)

def _filter_route(df: pd.DataFrame, pattern: str) -> pd.DataFrame:
    if df.empty or "route_id" not in df.columns:
        return df
    return df[df["route_id"].str.contains(pattern, case=False, na=False)]

if route_filter:
    vp_df = _filter_route(vp_df, route_filter)
    tu_df = _filter_route(tu_df, route_filter)
    ts_df = _filter_route(ts_df, route_filter)
    route_df = _filter_route(route_df, route_filter)


# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------
tab_live, tab_hist, tab_routes, tab_api = st.tabs([
    "Live Overview", "Historical Trends", "Route Analysis", "ML Serving"
])


# ── Tab 1: Live Overview ────────────────────────────────────────────────────
with tab_live:
    # Key metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Active vehicles (window)", vp_df["vehicle_id"].nunique() if not vp_df.empty else 0)
    with col2:
        st.metric("Active routes", tu_df["route_id"].nunique() if not tu_df.empty else 0)
    with col3:
        avg_delay = tu_df["delay_s"].mean() if not tu_df.empty else 0
        st.metric("Avg delay (s)", f"{avg_delay:.0f}")
    with col4:
        max_delay = tu_df["delay_s"].max() if not tu_df.empty else 0
        st.metric("Max delay (s)", f"{max_delay:.0f}")

    st.divider()

    col_map, col_delays = st.columns([1, 1])

    with col_map:
        st.subheader("Live vehicle positions")
        # Get latest position per vehicle
        if not vp_df.empty:
            latest_vp = (
                vp_df.dropna(subset=["lat", "lon"])
                .sort_values("polled_at")
                .drop_duplicates(subset=["vehicle_id"], keep="last")
            )
            if not latest_vp.empty:
                fig_map = px.scatter_map(
                    latest_vp,
                    lat="lat",
                    lon="lon",
                    color="route_id",
                    hover_name="vehicle_id",
                    hover_data=["trip_id", "speed", "polled_at"],
                    zoom=13,
                    height=420,
                )
                fig_map.update_layout(margin=dict(l=0, r=0, t=0, b=0))
                st.plotly_chart(fig_map, use_container_width=True)
            else:
                st.info("Vehicle positions have no lat/lon data yet.")
        else:
            st.info("No vehicle position data in this window. Is the poller running?")

    with col_delays:
        st.subheader("Current delays by route")
        if not tu_df.empty:
            # Latest delay per route
            latest_tu = (
                tu_df.sort_values("polled_at")
                .drop_duplicates(subset=["route_id"], keep="last")
            )
            fig_bar = px.bar(
                latest_tu.sort_values("delay_s", ascending=True),
                x="delay_s",
                y="route_id",
                orientation="h",
                color="delay_s",
                color_continuous_scale=["green", "gold", "red"],
                labels={"delay_s": "Delay (seconds)", "route_id": "Route"},
                height=420,
            )
            fig_bar.update_layout(
                margin=dict(t=20, b=20),
                showlegend=False,
                coloraxis_showscale=False,
            )
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("No trip update data in this window.")

    # Recent raw data
    with st.expander("Recent vehicle positions (raw)"):
        if not vp_df.empty:
            st.dataframe(vp_df.head(100), use_container_width=True)
        else:
            st.write("No data.")

    with st.expander("Recent trip updates (raw)"):
        if not tu_df.empty:
            st.dataframe(tu_df.head(100), use_container_width=True)
        else:
            st.write("No data.")


# ── Tab 2: Historical Trends ────────────────────────────────────────────────
with tab_hist:
    st.subheader(f"Delay trends — last {lookback_hours}h (5-min buckets)")
    if not ts_df.empty:
        fig_ts = px.line(
            ts_df,
            x="time",
            y="mean_delay",
            color="route_id",
            labels={"mean_delay": "Mean delay (s)", "time": "Time (UTC)"},
            height=400,
        )
        fig_ts.update_layout(margin=dict(t=20, b=20))
        st.plotly_chart(fig_ts, use_container_width=True)
    else:
        st.info("Not enough data for trend analysis. Let the poller run to accumulate history.")

    st.divider()

    col_dist, col_heat = st.columns(2)

    with col_dist:
        st.subheader("Delay distribution")
        if not tu_df.empty:
            fig_hist = px.histogram(
                tu_df,
                x="delay_s",
                color="route_id",
                nbins=40,
                labels={"delay_s": "Delay (seconds)"},
                height=350,
                barmode="overlay",
                opacity=0.7,
            )
            fig_hist.update_layout(margin=dict(t=20, b=20))
            st.plotly_chart(fig_hist, use_container_width=True)
        else:
            st.info("No delay data.")

    with col_heat:
        st.subheader("Delay heatmap (route vs hour)")
        if not tu_df.empty and len(tu_df) > 10:
            heat = tu_df.copy()
            heat["hour"] = heat["polled_at"].dt.hour
            pivot = heat.groupby(["route_id", "hour"])["delay_s"].mean().reset_index()
            pivot_wide = pivot.pivot(index="route_id", columns="hour", values="delay_s")
            fig_heat = px.imshow(
                pivot_wide,
                labels=dict(x="Hour of day", y="Route", color="Avg delay (s)"),
                color_continuous_scale="RdYlGn_r",
                aspect="auto",
                height=350,
            )
            fig_heat.update_layout(margin=dict(t=20, b=20))
            st.plotly_chart(fig_heat, use_container_width=True)
        else:
            st.info("Not enough data for heatmap.")


# ── Tab 3: Route Analysis ───────────────────────────────────────────────────
with tab_routes:
    st.subheader("Route summary")
    if not route_df.empty:
        st.dataframe(
            route_df.rename(columns={
                "route_id": "Route",
                "obs": "Observations",
                "mean_delay": "Mean delay (s)",
                "min_delay": "Min delay (s)",
                "max_delay": "Max delay (s)",
                "first_seen": "First seen",
                "last_seen": "Last seen",
            }),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No route data in this window.")

    st.divider()

    if not tu_df.empty:
        st.subheader("Delay by route — box plot")
        fig_box = px.box(
            tu_df,
            x="route_id",
            y="delay_s",
            color="route_id",
            labels={"delay_s": "Delay (seconds)", "route_id": "Route"},
            height=400,
        )
        fig_box.update_layout(margin=dict(t=20, b=20), showlegend=False)
        st.plotly_chart(fig_box, use_container_width=True)


# ── Tab 4: ML Serving ───────────────────────────────────────────────────────
with tab_api:
    if not api_online:
        st.warning(
            "Serving API is offline. Start it with: "
            "`uvicorn serving.app:app --host 0.0.0.0 --port 8000`"
        )

    logs_df = fetch_serving_logs(limit=200)

    if not logs_df.empty:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Predictions served", len(logs_df))
        with col2:
            st.metric("Avg latency (ms)", f"{logs_df['latency_ms'].mean():.1f}")
        with col3:
            st.metric("p95 latency (ms)", f"{logs_df['latency_ms'].quantile(0.95):.1f}")

        st.divider()

        col_pred, col_lat = st.columns(2)
        with col_pred:
            st.subheader("Predicted delays over time")
            fig_pred = px.line(
                logs_df.sort_values("timestamp"),
                x="timestamp",
                y="predicted_delay_seconds",
                color="route_id",
                labels={"predicted_delay_seconds": "Delay (s)", "timestamp": "Time"},
                height=320,
            )
            fig_pred.update_layout(margin=dict(t=20, b=20))
            st.plotly_chart(fig_pred, use_container_width=True)

        with col_lat:
            st.subheader("Request latency")
            fig_lat = go.Figure()
            fig_lat.add_trace(go.Histogram(
                x=logs_df["latency_ms"], nbinsx=20,
                marker_color="#4F8EF7", name="Latency (ms)",
            ))
            fig_lat.update_layout(
                xaxis_title="Latency (ms)", yaxis_title="Count",
                height=320, margin=dict(t=20, b=20),
            )
            st.plotly_chart(fig_lat, use_container_width=True)

        with st.expander("Raw prediction log"):
            st.dataframe(logs_df.sort_values("timestamp", ascending=False), use_container_width=True)
    else:
        st.info("No prediction logs yet. Send requests to the serving API to see stats here.")


# ---------------------------------------------------------------------------
# Auto-refresh
# ---------------------------------------------------------------------------
if auto_refresh:
    import time
    time.sleep(REFRESH_SECONDS)
    st.rerun()
