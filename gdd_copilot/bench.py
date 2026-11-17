"""Planted-claim benchmark for the citation checker.

Builds known-false claims (and known-true controls) from the Steam snapshots, plants them as
bullets inside a real generated GDD, runs the checker on the whole document and reports which
planted lines were flagged. Deterministic: same corpus + same GDD -> same numbers.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from .check import check_markdown


def _facts(snap: dict) -> dict:
    d = snap["details"]
    return {"aid": snap["appid"], "name": d["name"], "dev": d["developers"][0], "pub": d["publishers"][0],
            "date": d["release_date"], "rec": d["recommendations"], "plat": d["platforms"]}


def planted_claims(snaps: list[dict]) -> list[tuple[str, str, bool]]:
    """Return (category, bullet sentence, should_be_flagged)."""
    games = [_facts(s) for s in sorted(snaps, key=lambda s: s["appid"])]
    out = []
    for i, g in enumerate(games):
        o = games[(i + 1) % len(games)]  # a different reference game
        fid = f"S{g['aid']}-facts"
        name, dev, rec = g["name"], g["dev"], f"{g['rec']:,}"
        cite = lambda q, cid=fid: f'[[{cid}: "{q}"]]'
        out += [
            ("control", f"{name} was developed by {dev}. {cite('Developer: ' + dev)}", False),
            ("control", f"{name} was released on {g['date']}. {cite('Release date: ' + g['date'])}", False),
            ("control", f"{name} has {rec} Steam user recommendations. {cite('Steam user recommendations: ' + rec)}", False),
            ("control", f"{name} was published by {g['pub'].rstrip('.')}. {cite('Publisher: ' + g['pub'])}", False),
            ("uncited", f"{name} sold more than {i + 2} million copies in its first year.", True),
            ("fake_quote", f"{name} includes a four-player online co-op mode. {cite('four-player online co-op mode')}", True),
            ("bad_chunk", f"{name} was developed by {dev}. {cite('Developer: ' + dev, fid.replace('-facts', '-dev'))}", True),
            ("number_mismatch", f"{name} has {g['rec'] * 2:,} Steam user recommendations. {cite('Steam user recommendations: ' + rec)}", True),
            ("entity_mismatch", f"{o['name']} was developed by {dev}. {cite('Developer: ' + dev)}", True),
            ("off_topic", f"{name} lets players build and share custom levels with a full map editor. {cite('Developer: ' + dev)}", True),
            ("inversion", f"{name} does not run on {g['plat'][0]}. {cite('Platforms: ' + ', '.join(g['plat']))}", True),
            ("wrong_company", f"{name} was published by {o['pub'].rstrip('.')}. {cite('Publisher: ' + g['pub'])}", True),
        ]
    return out


def run(gdd_md: str, chunks, snaps: list[dict], seed: int = 7) -> dict:
    planted = planted_claims(snaps)
    lines = gdd_md.splitlines()
    bullet_lines = [i for i, l in enumerate(lines) if l.startswith("- ")]
    rng = random.Random(seed)
    inserts = sorted(((rng.choice(bullet_lines), k) for k in range(len(planted))), reverse=True)
    for at, k in inserts:
        lines.insert(at + 1, f"- {planted[k][1]}")
    doc = "\n".join(lines)
    results = check_markdown(doc, chunks)
    # map each planted sentence back to its check result by exact text
    by_text = {r.sentence: r for r in results}
    stats: dict[str, dict] = {}
    rows = []
    for cat, sent, should in planted:
        r = by_text[sent]
        flagged = r.status != "supported"
        st = stats.setdefault(cat, {"n": 0, "flagged": 0})
        st["n"] += 1
        st["flagged"] += flagged
        rows.append({"category": cat, "sentence": sent, "status": r.status, "flagged": flagged})
    planted_n = sum(v["n"] for k, v in stats.items() if k != "control")
    caught = sum(v["flagged"] for k, v in stats.items() if k != "control")
    return {
        "planted": planted_n,
        "caught": caught,
        "catch_rate": round(caught / planted_n, 3),
        "controls": stats["control"]["n"],
        "controls_false_flagged": stats["control"]["flagged"],
        "by_category": {k: {**v, "rate": round(v["flagged"] / v["n"], 3)} for k, v in stats.items()},
        "rows": rows,
    }


def load_snaps(steam_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(steam_dir.glob("*.json"))]
