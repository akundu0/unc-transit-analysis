"""
Data retention policy — delete rows older than RETENTION_DAYS to stay within
the Supabase free tier (500 MB).

Can be run standalone or called from the poller on a schedule.

Usage:
    python scripts/retention.py                  # uses RETENTION_DAYS env var (default 30)
    python scripts/retention.py --days 14        # explicit override
    python scripts/retention.py --dry-run        # show what would be deleted
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import text

from storage.db import engine

load_dotenv()

log = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "30"))


_ALLOWED_TABLES = frozenset({"vehicle_positions", "trip_updates"})

# Pre-built SQL text objects keyed by (table, action) — avoids f-string interpolation.
_RETENTION_SQL = {
    (t, "count"): text(f"SELECT COUNT(*) FROM {t} WHERE polled_at < :cutoff")
    for t in _ALLOWED_TABLES
} | {
    (t, "delete"): text(f"DELETE FROM {t} WHERE polled_at < :cutoff")
    for t in _ALLOWED_TABLES
}


def purge_old_rows(retention_days: int = DEFAULT_RETENTION_DAYS, dry_run: bool = False) -> dict:
    """Delete rows with polled_at older than `retention_days` days ago.

    Returns {"vehicle_positions": n, "trip_updates": m} with counts deleted.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = {}

    with engine.begin() as conn:
        for table in _ALLOWED_TABLES:
            if dry_run:
                count = conn.execute(
                    _RETENTION_SQL[(table, "count")], {"cutoff": cutoff}
                ).scalar()
                log.info("[retention] DRY RUN: would delete %d rows from %s (older than %s)",
                         count, table, cutoff.isoformat())
                result[table] = 0
            else:
                res = conn.execute(
                    _RETENTION_SQL[(table, "delete")], {"cutoff": cutoff}
                )
                deleted = res.rowcount
                if deleted > 0:
                    log.info("[retention] Deleted %d rows from %s (older than %s)",
                             deleted, table, cutoff.isoformat())
                else:
                    log.info("[retention] No rows to delete from %s", table)
                result[table] = deleted

    return result


def main():
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    parser = argparse.ArgumentParser(description="Purge old transit data rows")
    parser.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS,
                        help=f"Delete rows older than N days (default: {DEFAULT_RETENTION_DAYS})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be deleted without actually deleting")
    args = parser.parse_args()

    result = purge_old_rows(retention_days=args.days, dry_run=args.dry_run)

    total = sum(result.values())
    if args.dry_run:
        print(f"\nDry run complete. Would delete {total} total rows.")
    else:
        print(f"\nRetention complete. Deleted {total} total rows.")
    for table, count in result.items():
        print(f"  {table}: {count}")


if __name__ == "__main__":
    main()
