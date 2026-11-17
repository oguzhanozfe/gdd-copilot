"""Snapshot public Steam data for reference games.

Uses two public store endpoints:
  - appdetails: https://store.steampowered.com/api/appdetails?appids=<id>
  - appreviews: https://store.steampowered.com/appreviews/<id>?json=1

Both paths are allowed by store.steampowered.com/robots.txt (checked at run
time). Requests are spaced by MIN_INTERVAL seconds. Reviewer identities
(steamid, profile) are dropped; only review text, vote and playtime fields are
kept, and review text is truncated.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

STORE = "https://store.steampowered.com"
USER_AGENT = "gdd-copilot/0.1 (+https://github.com/oguzhanozfe/gdd-copilot)"
MIN_INTERVAL = 2.0
REVIEW_CHARS = 600

_last = 0.0
_robots: urllib.robotparser.RobotFileParser | None = None


def _allowed(url: str) -> bool:
    global _robots
    if _robots is None:
        _robots = urllib.robotparser.RobotFileParser(STORE + "/robots.txt")
        _robots.read()
    return _robots.can_fetch(USER_AGENT, url)


def _get_json(url: str) -> dict | None:
    global _last
    if not _allowed(url):
        raise PermissionError(f"robots.txt disallows {url}")
    wait = MIN_INTERVAL - (time.monotonic() - _last)
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.geturl().rstrip("/") == STORE:  # Steam answers refusals with a redirect to the homepage
            return None
        body = r.read()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def strip_html(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t]+", " ", s)
    return "\n".join(line.strip() for line in s.splitlines() if line.strip())


def snapshot(appid: int, num_reviews: int = 20) -> dict:
    """Fetch one game and return a self-describing snapshot dict."""
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    d_url = f"{STORE}/api/appdetails?appids={appid}&l=english&cc=us"
    raw = (_get_json(d_url) or {}).get(str(appid), {})
    if not raw.get("success"):
        raise RuntimeError(f"appdetails failed for {appid}")
    d = raw["data"]
    details = {
        "name": d.get("name"),
        "short_description": strip_html(d.get("short_description", "")),
        "about_the_game": strip_html(d.get("about_the_game", ""))[:4000],
        "developers": d.get("developers", []),
        "publishers": d.get("publishers", []),
        "release_date": (d.get("release_date") or {}).get("date"),
        "is_free": d.get("is_free"),
        "price": (d.get("price_overview") or {}).get("final_formatted"),
        "genres": [g["description"] for g in d.get("genres", [])],
        "categories": [c["description"] for c in d.get("categories", [])],
        "platforms": [k for k, v in (d.get("platforms") or {}).items() if v],
        "metacritic": (d.get("metacritic") or {}).get("score"),
        "recommendations": (d.get("recommendations") or {}).get("total"),
        "dlc_count": len(d.get("dlc", [])),
    }
    q = urllib.parse.urlencode({"json": 1, "language": "english", "filter": "all",
                                "purchase_type": "all", "num_per_page": num_reviews})
    r_url = f"{STORE}/appreviews/{appid}?{q}"
    rv = _get_json(r_url)
    reviews = None
    if rv and rv.get("success") == 1:
        reviews = {
            "summary": {k: rv["query_summary"].get(k) for k in
                        ("review_score_desc", "total_positive", "total_negative", "total_reviews")},
            "items": [{
                "recommendationid": x.get("recommendationid"),
                "voted_up": x.get("voted_up"),
                "votes_up": x.get("votes_up"),
                "playtime_hours": round((x.get("author") or {}).get("playtime_forever", 0) / 60),
                "text": " ".join((x.get("review") or "").split())[:REVIEW_CHARS],
            } for x in rv.get("reviews", [])],
        }
    return {
        "appid": appid,
        "retrieved_at": now,
        "sources": {"appdetails": d_url, "appreviews": r_url if reviews else None},
        "store_url": f"{STORE}/app/{appid}/",
        "details": details,
        "reviews": reviews,
        "reviews_note": None if reviews else "appreviews endpoint did not return data at retrieval time",
    }


def save(appids: list[int], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for a in appids:
        p = out_dir / f"{a}.json"
        p.write_text(json.dumps(snapshot(a), indent=2, ensure_ascii=False) + "\n")
        paths.append(p)
    return paths
