"""Turn Steam snapshots and Markdown notes into citable chunks, and search them with BM25."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

WORD = re.compile(r"[a-z0-9]+")
STOP = set("""a an and are as at be by for from has have in is it its of on or that the this to was
were will with you your can each their they them more than into all any not but our we""".split())


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    source: str        # human label, e.g. "Steam store: Slay the Spire"
    url: str
    retrieved: str     # ISO date of the snapshot, or "user notes"
    game: str | None = None  # reference game name if the chunk is about one game


def tokens(s: str) -> list[str]:
    return [t for t in WORD.findall(s.lower()) if t not in STOP]


def _sentences(text: str, per_chunk: int = 3) -> list[str]:
    lines = [l for l in text.splitlines() if l.strip()]
    out, buf = [], []
    for line in lines:
        buf.append(line)
        if len(buf) >= per_chunk:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return out


def steam_chunks(snap: dict) -> list[Chunk]:
    d, aid = snap["details"], snap["appid"]
    name, date = d["name"], snap["retrieved_at"][:10]
    url = snap["store_url"]
    src = f"Steam store page data: {name}"
    rec = f"{d['recommendations']:,}" if d.get("recommendations") else "n/a"
    facts = [
        f"{name} (Steam app {aid}).",
        f"Developer: {', '.join(d['developers']) or 'n/a'}.",
        f"Publisher: {', '.join(d['publishers']) or 'n/a'}.",
        f"Release date: {d['release_date']}.",
        f"Price on the US store: {'Free to play' if d['is_free'] else d['price'] or 'n/a'}.",
        f"Genres: {', '.join(d['genres'])}.",
        f"Platforms: {', '.join(d['platforms'])}.",
        f"Metacritic score: {d['metacritic'] or 'n/a'}.",
        f"Steam user recommendations: {rec}.",
        f"DLC listed: {d['dlc_count']}.",
    ]
    chunks = [
        Chunk(f"S{aid}-facts", " ".join(facts), src, snap["sources"]["appdetails"], date, name),
        Chunk(f"S{aid}-features", f"{name} store features: {', '.join(d['categories'])}.", src,
              snap["sources"]["appdetails"], date, name),
        Chunk(f"S{aid}-short", f"{name}: {d['short_description']}", src, url, date, name),
    ]
    for i, part in enumerate(_sentences(d["about_the_game"]), 1):
        chunks.append(Chunk(f"S{aid}-about{i}", f"{name}: {part}", src, url, date, name))
    rv = snap.get("reviews")
    if rv:
        s = rv["summary"]
        chunks.append(Chunk(
            f"S{aid}-reviews",
            f"{name} Steam reviews (English): {s['review_score_desc']}, "
            f"{s['total_positive']:,} positive and {s['total_negative']:,} negative of {s['total_reviews']:,}.",
            f"Steam reviews: {name}", snap["sources"]["appreviews"], date, name))
        for i, r in enumerate(rv["items"], 1):
            verdict = "Recommended" if r["voted_up"] else "Not recommended"
            chunks.append(Chunk(
                f"S{aid}-r{i}", f"{name} player review ({verdict}, {r['playtime_hours']} h played): {r['text']}",
                f"Steam review {r['recommendationid']}: {name}", snap["sources"]["appreviews"], date, name))
    return chunks


def notes_chunks(path: Path) -> list[Chunk]:
    """One chunk per paragraph or bullet of a Markdown notes file."""
    stem = path.stem
    blocks, buf = [], []
    for line in path.read_text().splitlines() + [""]:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(("- ", "* ")):
            if buf:
                blocks.append(" ".join(buf))
                buf = []
            if s.startswith(("- ", "* ")):
                buf.append(s[2:])
        else:
            buf.append(s)
    return [Chunk(f"N-{stem}-{i}", b, f"User notes: {path.name}", path.name, "user notes")
            for i, b in enumerate(blocks, 1)]


def load(steam_dir: Path, appids: list[int] | None = None, notes: list[Path] = ()) -> list[Chunk]:
    out: list[Chunk] = []
    for p in sorted(steam_dir.glob("*.json")):
        snap = json.loads(p.read_text())
        if appids is None or snap["appid"] in appids:
            out += steam_chunks(snap)
    for n in notes:
        out += notes_chunks(Path(n))
    return out


class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.docs = [Counter(tokens(c.text)) for c in chunks]
        self.len = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.len) / max(len(self.len), 1)
        df = Counter(t for d in self.docs for t in d)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def search(self, query: str, k: int = 8) -> list[Chunk]:
        q = tokens(query)
        scored = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [self.chunks[i] for _, i in scored[:k]]
