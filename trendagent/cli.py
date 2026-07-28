"""Command line entry point: python -m trendagent <command>"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timedelta, timezone

from . import ingest, store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trendagent")
    parser.add_argument("--db", default=os.getenv("TRENDAGENT_DB", "trends.db"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the database schema")

    p_ingest = sub.add_parser("ingest", help="fetch from sources and record observations")
    p_ingest.add_argument("--source", choices=sorted(ingest.SOURCES), default=None)

    p_status = sub.add_parser("status", help="show what has accumulated so far")
    p_status.add_argument("--limit", type=int, default=15)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    conn = store.connect(args.db)
    store.init_schema(conn)

    if args.command == "init":
        print(f"schema ready at {args.db}")

    elif args.command == "ingest":
        for name, count in ingest.run(conn, only=args.source).items():
            print(f"{name}: {'FAILED' if count < 0 else f'{count} items'}")

    elif args.command == "status":
        counts = conn.execute(
            "SELECT source, COUNT(*) n FROM items GROUP BY source"
        ).fetchall()
        obs = conn.execute("SELECT COUNT(*) n FROM observations").fetchone()["n"]
        runs = conn.execute(
            "SELECT COUNT(*) n FROM ingest_runs WHERE finished_at IS NOT NULL AND error IS NULL"
        ).fetchone()["n"]

        print(f"{obs} observations / {sum(r['n'] for r in counts)} items / {runs} runs")
        for row in counts:
            print(f"  {row['source']:<8} {row['n']}")

        # Rank by velocity, not recency. Sorting by last_seen_at lets whichever
        # source ran last monopolise the list, which hides entire sources.
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=48)).isoformat()
        rows = conn.execute(
            "SELECT id, source, title FROM items WHERE last_seen_at >= ?", (cutoff,)
        ).fetchall()

        scored = []
        for row in rows:
            metric = "stars" if row["source"] == "github" else "points"
            v = store.velocity(conn, row["id"], metric=metric)
            if v is not None:
                scored.append((v, row["source"], metric, row["title"]))
        scored.sort(reverse=True)

        print(f"\ntop movers ({len(scored)} items with enough history):")
        for v, source, metric, title in scored[: args.limit]:
            print(f"  {v:>7.1f} {metric[:3]}/hr  [{source:<6}] {title[:62]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
