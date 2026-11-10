"""FastAPI application for the Live Market Workstation.

Run from the project root:
    python3 -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
"""

from fastapi import FastAPI

from backend.history import load_candles

app = FastAPI(title="Live Market Workstation")


@app.get("/api/health")
def health() -> dict:
    """Small status response used to check that the backend is reachable."""
    return {"status": "ok", "service": "backend"}


@app.get("/api/history")
def history() -> list[dict]:
    """Synthetic one-minute candles from prompts/data/sample-candles.csv, ordered by timestamp."""
    return load_candles()
