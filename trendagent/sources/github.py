"""GitHub ingestion -- repositories with early traction.

GitHub has no official "trending" endpoint; the trending page is rendered
server-side and has always been scraped. That turns out to be fine, because
scraping a trending snapshot gives you a ranking someone else computed with
undisclosed weights. Re-observing star counts on a schedule gives you the
derivative yourself, which is the number you actually want.

Auth is optional but worth setting: unauthenticated search is limited to
roughly 10 requests/minute, authenticated to 30.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from ..store import Item

UTC = timezone.utc
ENDPOINT = "https://api.github.com/search/repositories"


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "trendagent/0.1",
    }
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch(
    created_within_days: int = 90,
    min_stars: int = 100,
    per_page: int = 50,
    timeout: float = 20.0,
) -> list[Item]:
    """Repos created recently that have already accumulated stars.

    The `created_within_days` window is what keeps this from returning React
    and Linux every single run. We want things that are new *and* moving, not
    things that are merely large.
    """
    created_since = (datetime.now(tz=UTC) - timedelta(days=created_within_days)).date()
    query = f"created:>{created_since.isoformat()} stars:>{min_stars}"

    with httpx.Client(timeout=timeout, headers=_headers()) as client:
        resp = client.get(
            ENDPOINT,
            params={"q": query, "sort": "stars", "order": "desc", "per_page": per_page},
        )
        resp.raise_for_status()
        data = resp.json()

    items: list[Item] = []
    for repo in data.get("items", []):
        items.append(
            Item(
                source="github",
                external_id=str(repo["id"]),
                url=repo["html_url"],
                title=repo["full_name"],
                author=repo.get("owner", {}).get("login"),
                published_at=repo.get("created_at"),
                payload={
                    "description": repo.get("description"),
                    "language": repo.get("language"),
                    "topics": repo.get("topics", []),
                },
                metrics={
                    "stars": repo.get("stargazers_count") or 0,
                    "forks": repo.get("forks_count") or 0,
                },
            )
        )

    return items
