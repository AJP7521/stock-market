"""Configured indicator instances, stored in SQLite so a chart configuration can be reopened.

Each row is one named instance of an indicator type with its own settings, for one
symbol (the chart). Names are unique per symbol. Settings are validated by
backend.indicators.validate_settings before they are stored.
"""

import json
import sqlite3

from backend.indicators import validate_settings
from backend.recorder import now_ms

SCHEMA = """
CREATE TABLE IF NOT EXISTS indicator_configs (
    id          INTEGER PRIMARY KEY,
    symbol      TEXT NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    settings    TEXT NOT NULL,          -- JSON object, validated
    created_ms  INTEGER NOT NULL,
    UNIQUE (symbol, name)
);
"""


def _ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _row(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "symbol": r["symbol"], "name": r["name"], "type": r["type"],
            "settings": json.loads(r["settings"]), "created_ms": r["created_ms"]}


def list_configs(conn: sqlite3.Connection, symbol: str) -> list[dict]:
    _ensure(conn)
    conn.row_factory = sqlite3.Row
    return [_row(r) for r in conn.execute(
        "SELECT * FROM indicator_configs WHERE symbol = ? ORDER BY id", (symbol,))]


def add_config(conn: sqlite3.Connection, symbol: str, name: str, type_key: str, settings: dict) -> dict:
    """Validate and store one indicator instance. Raises ValueError with a readable message."""
    _ensure(conn)
    name = (name or "").strip()
    if not name:
        raise ValueError("give the indicator a name")
    if len(name) > 40:
        raise ValueError("name must be 40 characters or fewer")
    clean = validate_settings(type_key, settings)
    try:
        with conn:
            new_id = conn.execute(
                "INSERT INTO indicator_configs (symbol, name, type, settings, created_ms) VALUES (?, ?, ?, ?, ?)",
                (symbol, name, type_key, json.dumps(clean, sort_keys=True), now_ms())).lastrowid
    except sqlite3.IntegrityError:
        raise ValueError(f"{symbol} already has an indicator named {name!r}")
    conn.row_factory = sqlite3.Row
    return _row(conn.execute("SELECT * FROM indicator_configs WHERE id = ?", (new_id,)).fetchone())


def delete_config(conn: sqlite3.Connection, config_id: int) -> bool:
    _ensure(conn)
    with conn:
        return conn.execute("DELETE FROM indicator_configs WHERE id = ?", (config_id,)).rowcount == 1
