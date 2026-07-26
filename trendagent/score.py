"""Ranking.

The core problem this solves: HN points and GitHub stars are not comparable.
A hot thread might gain 30 points/hr while a hot repo gains 85 stars/hr, and
neither number means anything relative to the other. Ranking on raw velocity
would just mean GitHub always wins.

So each item is scored against *its own source's* distribution -- percentile
rank rather than absolute rate. Percentile is used instead of a z-score
deliberately: these distributions are heavy-tailed power laws, and a single
viral outlier would distort a mean-and-standard-deviation model badly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import store

UTC = timezone.utc

METRIC_FOR_SOURCE = {"github": "stars", "hn": "points"}


@dataclass
class Ranked:
    item: sqlite3.Row
    velocity: float
    percentile: float
    score: float
    metric: str
    age_hours: float
    series: list[tuple[str, float]]


def _percentile(sorted_values: list[float], value: float) -> float:
    """Fraction of the distribution at or below `value`, in [0, 1]."""
    if not sorted_values:
        return 0.0
    below = sum(1 for v in sorted_values if v <= value)
    return below / len(sorted_values)


def rank(
    conn: sqlite3.Connection,
    window_hours: int = 48,
    half_life_hours: float = 72.0,
    limit: int = 60,
) -> list[Ranked]:
    """Rank undismissed items by normalized velocity, decayed by age.

    Decay exists so a genuinely fast mover from four days ago doesn't sit at
    the top forever. Half-life is deliberately long relative to the ingest
    cadence -- this is a page you might not open for a few days, and things
    shouldn't vanish just because you were busy.
    """
    cutoff = (datetime.now(tz=UTC) - timedelta(hours=window_hours)).isoformat()
    rows = conn.execute(
        """
        SELECT i.* FROM items i
        LEFT JOIN item_state s ON s.item_id = i.id
        WHERE i.last_seen_at >= ?
          AND (s.state IS NULL OR s.state = 'saved')
        """,
        (cutoff,),
    ).fetchall()

    # First pass: velocity for everything, bucketed by source.
    measured: list[tuple[sqlite3.Row, float, str]] = []
    by_source: dict[str, list[float]] = {}

    for row in rows:
        metric = METRIC_FOR_SOURCE.get(row["source"], "points")
        v = store.velocity(conn, row["id"], metric=metric, window_hours=window_hours)
        if v is None:
            continue  # not enough history yet
        measured.append((row, v, metric))
        by_source.setdefault(row["source"], []).append(v)

    for values in by_source.values():
        values.sort()

    # Second pass: normalize within source, apply decay.
    now_dt = datetime.now(tz=UTC)
    ranked: list[Ranked] = []

    for row, v, metric in measured:
        pct = _percentile(by_source[row["source"]], v)

        stamp = row["first_seen_at"]
        try:
            age = (now_dt - datetime.fromisoformat(stamp)).total_seconds() / 3600
        except (TypeError, ValueError):
            age = 0.0
        age = max(age, 0.0)

        decay = 0.5 ** (age / half_life_hours)

        ranked.append(
            Ranked(
                item=row,
                velocity=v,
                percentile=pct,
                score=pct * decay,
                metric=metric,
                age_hours=age,
                series=store.history(conn, row["id"], metric, hours=window_hours),
            )
        )

    ranked.sort(key=lambda r: r.score, reverse=True)
    return ranked[:limit]
