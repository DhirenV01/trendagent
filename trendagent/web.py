"""The page.

Server-rendered, no build step, no frontend framework. Every link routes
through /c/{id} so that opening something is recorded -- click history is the
training data for the eventual ranker, and it has the same no-backfill problem
as the observation series.

Run: uvicorn trendagent.web:app --reload
"""

from __future__ import annotations

import html
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import score, store

UTC = timezone.utc
DB = "trends.db"

app = FastAPI(title="trendagent")


def db():
    conn = store.connect(DB)
    store.init_schema(conn)
    return conn


# --------------------------------------------------------------------------
# sparkline
# --------------------------------------------------------------------------

def sparkline(series: list[tuple[str, float]], window_hours: int = 48,
              width: int = 150, height: int = 26) -> str:
    """Draw the observation series as an SVG path.

    The x-axis is the shared window, not the item's own span, so a repo first
    seen six hours ago draws a short trace on the right rather than stretching
    to fill the box. Traces across the page are therefore directly comparable
    -- that shared axis is what makes the list read as one instrument panel.
    """
    if len(series) < 2:
        return f'<svg class="spark" viewBox="0 0 {width} {height}"></svg>'

    now_ts = datetime.now(tz=UTC).timestamp()
    start_ts = now_ts - window_hours * 3600

    pts = []
    for stamp, value in series:
        try:
            t = datetime.fromisoformat(stamp).timestamp()
        except ValueError:
            continue
        pts.append((t, value))

    if len(pts) < 2:
        return f'<svg class="spark" viewBox="0 0 {width} {height}"></svg>'

    lo = min(v for _, v in pts)
    hi = max(v for _, v in pts)
    span = (hi - lo) or 1.0

    coords = []
    for t, value in pts:
        x = (t - start_ts) / (now_ts - start_ts) * width
        y = height - 2 - ((value - lo) / span) * (height - 4)
        coords.append(f"{max(0.0, min(x, width)):.1f},{y:.1f}")

    path = "M" + " L".join(coords)
    cx, cy = coords[-1].split(",")
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" aria-hidden="true">'
        f'<path d="{path}" fill="none" stroke="currentColor" stroke-width="1.4" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{cx}" cy="{cy}" r="2" fill="currentColor"/>'
        f"</svg>"
    )


def age_label(hours: float) -> str:
    if hours < 1:
        return "just now"
    if hours < 24:
        return f"{int(hours)}h"
    return f"{int(hours // 24)}d"


# --------------------------------------------------------------------------
# styles
# --------------------------------------------------------------------------

STYLE = """
:root {
  --paper: #E9ECE6;
  --ink:   #182420;
  --muted: #6E7A73;
  --rule:  #CFD6CD;
  --trace: #3A5A6B;
  --hot:   #7B3A62;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--paper); color: var(--ink);
  font-family: 'IBM Plex Sans', system-ui, sans-serif;
  font-size: 15px; line-height: 1.45;
}
.sheet { max-width: 940px; margin: 0 auto; padding: 40px 24px 96px; }
header { border-bottom: 2px solid var(--ink); padding-bottom: 14px; margin-bottom: 4px; }
h1 {
  font-family: 'Space Grotesk', system-ui, sans-serif;
  font-size: 30px; font-weight: 700; letter-spacing: -0.02em; margin: 0;
}
.readout {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 12px; color: var(--muted); margin-top: 6px;
  display: flex; gap: 18px; flex-wrap: wrap;
}
.axis {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 10px; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.08em; display: flex; justify-content: flex-end;
  padding: 10px 0 2px;
}
.axis span { width: 150px; display: flex; justify-content: space-between; }
.row {
  display: grid; grid-template-columns: 30px 78px 1fr 150px 62px;
  gap: 14px; align-items: center;
  padding: 11px 0; border-bottom: 1px solid var(--rule);
}
.rank {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 11px; color: var(--muted); text-align: right;
}
.rate {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 13px; font-variant-numeric: tabular-nums;
  text-align: right; color: var(--trace);
}
.row.hot .rate, .row.hot .spark { color: var(--hot); }
.rate small { display: block; font-size: 9px; color: var(--muted); letter-spacing: 0.05em; }
.title { min-width: 0; }
.title a {
  color: var(--ink); text-decoration: none; font-weight: 500;
  display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.title a:hover { text-decoration: underline; }
.title a:focus-visible { outline: 2px solid var(--hot); outline-offset: 3px; }
.meta {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 10.5px; color: var(--muted); text-transform: uppercase;
  letter-spacing: 0.06em; margin-top: 2px;
}
.spark { color: var(--trace); width: 150px; height: 26px; display: block; }
.acts { display: flex; gap: 8px; justify-content: flex-end; }
.acts button {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 11px; background: none; border: 1px solid var(--rule);
  color: var(--muted); cursor: pointer; padding: 3px 7px; border-radius: 2px;
}
.acts button:hover { border-color: var(--ink); color: var(--ink); }
.row.gone { opacity: 0.25; }
@media (prefers-reduced-motion: no-preference) {
  .row { transition: opacity 160ms ease; }
}
.empty {
  font-family: 'IBM Plex Mono', ui-monospace, monospace;
  font-size: 13px; color: var(--muted); padding: 48px 0; line-height: 1.7;
}
@media (max-width: 680px) {
  .row { grid-template-columns: 60px 1fr 62px; }
  .rank, .spark, .axis { display: none; }
}
"""

SCRIPT = """
async function mark(id, state, el) {
  await fetch('/state/' + id + '/' + state, { method: 'POST' });
  const row = el.closest('.row');
  row.classList.add('gone');
  setTimeout(() => row.remove(), 200);
}
"""


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> str:
    conn = db()
    ranked = score.rank(conn)

    total = conn.execute("SELECT COUNT(*) n FROM items").fetchone()["n"]
    obs = conn.execute("SELECT COUNT(*) n FROM observations").fetchone()["n"]
    clicks = conn.execute("SELECT COUNT(*) n FROM clicks").fetchone()["n"]

    if ranked:
        rows = []
        for i, r in enumerate(ranked, start=1):
            hot = " hot" if r.percentile >= 0.9 else ""
            title = html.escape(r.item["title"])
            rows.append(f"""
    <div class="row{hot}">
      <div class="rank">{i:02d}</div>
      <div class="rate">{r.velocity:+.1f}<small>{r.metric[:3]}/hr</small></div>
      <div class="title">
        <a href="/c/{r.item['id']}?rank={i}">{title}</a>
        <div class="meta">{r.item['source']} &middot; {age_label(r.age_hours)} old
          &middot; p{int(r.percentile * 100):02d}</div>
      </div>
      {sparkline(r.series)}
      <div class="acts">
        <button onclick="mark('{r.item['id']}','saved',this)" title="Save">keep</button>
        <button onclick="mark('{r.item['id']}','dismissed',this)" title="Dismiss">hide</button>
      </div>
    </div>""")
        body = f'<div class="axis"><span><b>-48h</b><b>now</b></span></div>' + "".join(rows)
    else:
        body = f"""<div class="empty">
No items have enough history to rate yet.<br>
Velocity needs at least two observations of the same item &mdash; run
<b>python -m trendagent ingest</b> twice, an hour apart.<br>
Currently holding {obs} observations across {total} items.
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>trendagent</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<style>{STYLE}</style>
</head><body>
<div class="sheet">
  <header>
    <h1>what's moving</h1>
    <div class="readout">
      <span>{obs} observations</span><span>{total} items</span>
      <span>{clicks} clicks logged</span>
      <span>{datetime.now(tz=UTC).strftime('%d %b %H:%M')} UTC</span>
    </div>
  </header>
  {body}
</div>
<script>{SCRIPT}</script>
</body></html>"""


@app.get("/c/{item_id}")
def click(item_id: str, rank: int | None = None):
    """Tracked redirect. Logs the click and its rank, then sends you onward."""
    conn = db()
    item = store.get_item(conn, item_id)
    if item is None:
        return RedirectResponse("/", status_code=302)
    store.record_click(conn, item_id, rank_position=rank, surface="page")
    return RedirectResponse(item["url"], status_code=302)


@app.post("/state/{item_id}/{state}")
def set_state(item_id: str, state: str):
    if state not in {"saved", "dismissed"}:
        return {"ok": False, "error": "state must be saved or dismissed"}
    store.set_state(db(), item_id, state)
    return {"ok": True}
