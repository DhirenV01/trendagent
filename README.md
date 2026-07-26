# trendagent

A personal signal-ranking system for tech news. Not an aggregator — it tracks
how fast topics are *moving* across sources at different stages of the
diffusion curve, and learns what you actually click.

## Quickstart

```bash
pip install -r requirements.txt
python -m trendagent init
python -m trendagent ingest
python -m trendagent status
```

Optional, raises the GitHub search rate limit from ~10/min to ~30/min:

```bash
export GITHUB_TOKEN=ghp_...
```

## Schedule it today

Velocity is a derivative. Until you have two observations of the same item you
have nothing, and until you have a few weeks you have no baseline to judge what
"fast" even means. Every day you delay is a day of history you cannot backfill.

Local, to start:

```
*/60 * * * *  cd /path/to/trendagent && /usr/bin/python3 -m trendagent ingest >> ingest.log 2>&1
```

Move to EventBridge → Lambda once you want it running without your laptop open.
Swap SQLite for Postgres or DynamoDB at the same time; `store.py` is the only
module that needs to change.

## Layout

```
trendagent/
  store.py            SQLite schema + velocity math
  ingest.py           orchestration, per-source failure isolation
  sources/
    hackernews.py     Algolia API — practitioner awareness stage
    github.py         search API — early adoption stage
  cli.py              init / ingest / status
```

## Design notes

**Observations are append-only.** Every run writes a new row per (item, metric)
rather than updating a counter. Current point counts are near-worthless as
signal — every popular thing has a high count. The delta is the signal.

**Sources are chosen to sit at different stages of diffusion**, so that a topic
appearing in several of them in sequence is evidence of a real trend rather
than an echo. HN and GitHub are stages 2 and 3; arXiv (earlier) and engineering
blogs (later) are the next two to add.

**Failures are isolated per source.** A gap in the time series is the one thing
this system can't repair after the fact, so one dead API must never take down
the run.

## Roadmap

1. ~~Ingest + storage~~ ← you are here; let it accumulate
2. Topic extraction and clustering — canonicalize "the same thing" across sources
3. Scoring: velocity × source weight × relevance, with recency decay
4. FastAPI page — persistent queue, dismiss/save state, tracked redirects
5. Daily SES digest — top 5 with tracked links, page holds the rest
6. Ranker trained on your own click data, replacing the static relevance score
