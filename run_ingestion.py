"""
Entry point for the ingestion poller.

Usage:
    python run_ingestion.py

Ensure the mock server is running first (for local testing):
    python -m ingestion.mock_server

Or set real GTFS-RT feed URLs in your .env file.
"""

import asyncio
from ingestion.poller import run_poller

if __name__ == "__main__":
    asyncio.run(run_poller())
