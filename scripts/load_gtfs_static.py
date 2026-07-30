"""
Download and parse Chapel Hill Transit static GTFS data.

Produces a JSON lookup file (data/gtfs_static/route_lookup.json) that maps
route_short_name → {route_id, long_name, color, text_color, url} and a
stop_lookup.json that maps stop_id → {name, lat, lon}.

These lookups are used by the dashboard to enrich realtime data with
human-readable route and stop names.

Usage:
    python scripts/load_gtfs_static.py          # uses cached zip if present
    python scripts/load_gtfs_static.py --force   # re-download from source
"""

import csv
import io
import json
import os
import sys
import zipfile
from pathlib import Path

GTFS_URL = "http://mychtransit.org/gtfs"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "gtfs_static"


def download_gtfs(force: bool = False) -> Path:
    """Download the GTFS zip if not already cached."""
    zip_path = DATA_DIR / "gtfs.zip"
    if zip_path.exists() and not force:
        print(f"[gtfs] Using cached {zip_path}")
        return zip_path

    import urllib.request

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[gtfs] Downloading {GTFS_URL} ...")
    urllib.request.urlretrieve(GTFS_URL, zip_path)
    print(f"[gtfs] Saved to {zip_path} ({zip_path.stat().st_size / 1024:.0f} KB)")
    return zip_path


def read_csv_from_zip(zip_path: Path, filename: str) -> list[dict]:
    """Read a CSV file from the GTFS zip archive."""
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(filename) as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            return list(reader)


def build_route_lookup(zip_path: Path) -> dict:
    """Build route_short_name → route metadata lookup."""
    rows = read_csv_from_zip(zip_path, "routes.txt")
    lookup = {}
    for row in rows:
        short_name = row.get("route_short_name", "").strip()
        if not short_name:
            continue
        # If there are duplicate short_names (e.g. two 'F' entries), keep the first
        if short_name in lookup:
            continue
        lookup[short_name] = {
            "route_id": row.get("route_id", ""),
            "long_name": row.get("route_long_name", ""),
            "color": f"#{row['route_color']}" if row.get("route_color") else None,
            "text_color": f"#{row['route_text_color']}" if row.get("route_text_color") else None,
            "url": row.get("route_url", ""),
        }
    return lookup


def build_stop_lookup(zip_path: Path) -> dict:
    """Build stop_id → stop metadata lookup."""
    rows = read_csv_from_zip(zip_path, "stops.txt")
    lookup = {}
    for row in rows:
        stop_id = row.get("stop_id", "").strip()
        if not stop_id:
            continue
        lookup[stop_id] = {
            "name": row.get("stop_name", ""),
            "lat": float(row["stop_lat"]) if row.get("stop_lat") else None,
            "lon": float(row["stop_lon"]) if row.get("stop_lon") else None,
        }
    return lookup


def main():
    force = "--force" in sys.argv

    zip_path = download_gtfs(force=force)

    # Extract zip for inspection
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(DATA_DIR)

    # Build lookups
    route_lookup = build_route_lookup(zip_path)
    stop_lookup = build_stop_lookup(zip_path)

    # Save JSON lookups
    route_json = DATA_DIR / "route_lookup.json"
    stop_json = DATA_DIR / "stop_lookup.json"

    with open(route_json, "w") as f:
        json.dump(route_lookup, f, indent=2)
    print(f"[gtfs] Route lookup: {len(route_lookup)} routes → {route_json}")

    with open(stop_json, "w") as f:
        json.dump(stop_lookup, f, indent=2)
    print(f"[gtfs] Stop lookup: {len(stop_lookup)} stops → {stop_json}")

    # Print summary
    print("\n── Route Summary ──")
    for short, meta in sorted(route_lookup.items()):
        print(f"  {short:6s}  {meta['color'] or '       ':9s}  {meta['long_name']}")


if __name__ == "__main__":
    main()
