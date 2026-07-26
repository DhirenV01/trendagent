"""Ingest orchestration.

Each source is run independently and failures are isolated -- one source being
down should never cost you the day's observations from the others. Gaps in the
time series are the one thing this system cannot recover from later.
"""

from __future__ import annotations

import logging
import sqlite3

from . import store
from .sources import github, hackernews

log = logging.getLogger(__name__)

SOURCES = {
    "hn": hackernews.fetch,
    "github": github.fetch,
}


def run(conn: sqlite3.Connection, only: str | None = None) -> dict[str, int]:
    results: dict[str, int] = {}
    targets = {only: SOURCES[only]} if only else SOURCES

    for name, fetcher in targets.items():
        run_id = store.start_run(conn, name)
        try:
            items = fetcher()
            written = store.save(conn, items)
            store.finish_run(conn, run_id, n_items=written)
            results[name] = written
            log.info("%s: ingested %d items", name, written)
        except Exception as exc:
            store.finish_run(conn, run_id, error=str(exc))
            results[name] = -1
            log.exception("%s: ingest failed", name)

    return results
