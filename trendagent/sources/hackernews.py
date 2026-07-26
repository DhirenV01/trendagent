"""Hacker News ingestion via the Algolia search API.

No API key, no auth, generous limits. Both `points` and `num_comments` come
back on every hit, which means quality signal arrives for free -- and the
ratio between them is its own signal (high comments relative to points tends
to mean controversy rather than consensus).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from ..store import Item

UTC = timezone.utc
ENDPOINT = "https://hn.algolia.com/api/v1/search_by_date"


def fetch(
    hours: int = 24,
    min_points: int = 20,
    max_pages: int = 5,
    timeout: float = 20.0,
) -> list[Item]:
    """Pull stories from the trailing `hours` window above a points floor.

    The floor matters more than it looks. HN publishes a few thousand stories a
    day and the overwhelming majority never clear single digits. Filtering at
    the API boundary keeps the database honest.
    """
    since = int((datetime.now(tz=UTC) - timedelta(hours=hours)).timestamp())
    items: list[Item] = []

    with httpx.Client(timeout=timeout, headers={"User-Agent": "trendagent/0.1"}) as client:
        for page in range(max_pages):
            resp = client.get(
                ENDPOINT,
                params={
                    "tags": "story",
                    "numericFilters": f"created_at_i>{since},points>{min_points}",
                    "hitsPerPage": 100,
                    "page": page,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            for hit in data.get("hits", []):
                # Ask HN / Show HN text posts have no external URL; point at the
                # discussion itself so the link still resolves.
                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
                items.append(
                    Item(
                        source="hn",
                        external_id=str(hit["objectID"]),
                        url=url,
                        title=hit.get("title") or "(untitled)",
                        author=hit.get("author"),
                        published_at=hit.get("created_at"),
                        payload={"tags": hit.get("_tags", [])},
                        metrics={
                            "points": hit.get("points") or 0,
                            "comments": hit.get("num_comments") or 0,
                        },
                    )
                )

            if page >= data.get("nbPages", 1) - 1:
                break
            time.sleep(0.3)

    return items
