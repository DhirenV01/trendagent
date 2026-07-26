"""SQLite persistence for trendagent.

Design note, and the reason this file exists before any ranking code:

We store *observations over time*, not just current state. An item's point
count right now is nearly worthless as a signal -- every popular thing has a
high count. The delta between this observation and the last one is the entire
signal. So `observations` is append-only: every ingest run writes a new row
per (item, metric), and velocity is computed as a difference over that series.

This is why ingestion needs to start running before anything else is built.
The ranker is useless until there is history for it to read.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    url           TEXT NOT NULL,
    title         TEXT NOT NULL,
    author        TEXT,
    published_at  TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    payload       TEXT,
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS observations (
    item_id     TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    observed_at TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL,
    PRIMARY KEY (item_id, observed_at, metric)
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    n_items     INTEGER DEFAULT 0,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_obs_lookup   ON observations(item_id, metric, observed_at);
CREATE INDEX IF NOT EXISTS idx_items_seen   ON items(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source, published_at);
"""


def now() -> str:
    """Current UTC time as an ISO8601 string. All timestamps in this DB are UTC."""
    return datetime.now(tz=UTC).isoformat()


def canonical_id(source: str, external_id: str) -> str:
    """Stable primary key. Keyed on source + the source's own id rather than URL,
    because the same story can surface under slightly different URLs."""
    return hashlib.sha1(f"{source}:{external_id}".encode()).hexdigest()[:16]


@dataclass
class Item:
    source: str
    external_id: str
    url: str
    title: str
    author: str | None = None
    published_at: str | None = None
    payload: dict = field(default_factory=dict)
    # metric name -> observed value at fetch time, e.g. {"points": 143, "comments": 62}
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return canonical_id(self.source, self.external_id)


def connect(path: str | Path = "trends.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def save(conn: sqlite3.Connection, items: list[Item], observed_at: str | None = None) -> int:
    """Upsert items and append one observation row per metric.

    Re-running this within the same minute is effectively idempotent: the
    observations primary key collapses duplicate (item, timestamp, metric) rows.
    """
    observed_at = observed_at or now()
    written = 0

    for item in items:
        conn.execute(
            """
            INSERT INTO items (id, source, external_id, url, title, author,
                               published_at, first_seen_at, last_seen_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                title        = excluded.title,
                payload      = excluded.payload
            """,
            (
                item.id, item.source, item.external_id, item.url, item.title,
                item.author, item.published_at, observed_at, observed_at,
                json.dumps(item.payload),
            ),
        )

        for metric, value in item.metrics.items():
            conn.execute(
                """
                INSERT OR REPLACE INTO observations (item_id, observed_at, metric, value)
                VALUES (?, ?, ?, ?)
                """,
                (item.id, observed_at, metric, float(value)),
            )
        written += 1

    conn.commit()
    return written


def velocity(
    conn: sqlite3.Connection,
    item_id: str,
    metric: str = "points",
    window_hours: int = 24,
) -> float | None:
    """Change in `metric` per hour over the trailing window.

    Returns None when there is not yet enough history -- which will be the case
    for everything on day one. That is expected, not a bug.
    """
    cutoff = (datetime.now(tz=UTC) - timedelta(hours=window_hours)).isoformat()
    rows = conn.execute(
        """
        SELECT observed_at, value FROM observations
        WHERE item_id = ? AND metric = ? AND observed_at >= ?
        ORDER BY observed_at
        """,
        (item_id, metric, cutoff),
    ).fetchall()

    if len(rows) < 2:
        return None

    first, last = rows[0], rows[-1]
    elapsed = (
        datetime.fromisoformat(last["observed_at"])
        - datetime.fromisoformat(first["observed_at"])
    ).total_seconds() / 3600

    if elapsed <= 0:
        return None
    return (last["value"] - first["value"]) / elapsed


def start_run(conn: sqlite3.Connection, source: str) -> int:
    cur = conn.execute(
        "INSERT INTO ingest_runs (source, started_at) VALUES (?, ?)", (source, now())
    )
    conn.commit()
    return cur.lastrowid


def finish_run(
    conn: sqlite3.Connection, run_id: int, n_items: int = 0, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE ingest_runs SET finished_at = ?, n_items = ?, error = ? WHERE id = ?",
        (now(), n_items, error, run_id),
    )
    conn.commit()
