"""Citation checker.

Every sentence in a GDD must be one of:
  - a proposal   ("Proposal: ...")       design opinion, allowed without citation
  - a question   ("Open question: ...")  allowed without citation
  - a claim with one or more citations [[CHUNK_ID: "exact quote"]]
    (a bare [[CHUNK_ID]] without a quote is reported as missing_quote)

A cited claim is SUPPORTED only if, for its citations together:
  1. every chunk id exists in the corpus            -> else bad_chunk
  2. every quote occurs verbatim in its chunk         -> else quote_not_found
     (case, whitespace, curly quotes and dashes normalised)
  3. every number in the claim appears in a cited chunk -> else number_mismatch
  4. every reference-game name in the claim appears in a cited chunk -> else entity_mismatch
  5. >= MIN_OVERLAP of the claim's content words appear in the cited chunks -> else low_overlap
Any other sentence is an uncited claim. Proposals and questions that name a reference game
without a quoted citation get a warning (warn_uncited_reference); they do not count as claims.

These are lexical checks. They catch fabricated quotes, wrong ids, wrong numbers, wrong
games and off-topic citations. They do not understand meaning: a claim that reuses the
chunk's words but inverts them ("does not support controllers") passes. See the README.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .corpus import Chunk, tokens

CITE = re.compile(r"\[\[\s*([A-Za-z0-9_.\-]+)\s*:\s*[\"“](.+?)[\"”]\s*\]\]")
NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")
MIN_OVERLAP = 0.5
PROPOSAL = re.compile(r"^\W*(proposal|design proposal)\s*:", re.I)
QUESTION = re.compile(r"^\W*open question\s*:", re.I)


def norm(s: str) -> str:
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-").replace("…", "...")
    return " ".join(s.lower().split())


def numbers(s: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUM.findall(s)}


@dataclass
class Result:
    sentence: str
    kind: str                 # claim | proposal | question
    status: str               # supported | uncited | missing_quote | bad_chunk | quote_not_found | number_mismatch | entity_mismatch | low_overlap | n/a
    citations: list[tuple[str, str]] = field(default_factory=list)
    detail: str = ""
    line: int = 0


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences without breaking inside [[...]] citations."""
    cites: list[str] = []

    def hide(m):
        cites.append(m.group(0))
        return f"\x00{len(cites) - 1}\x00"

    hidden = re.sub(r"\[\[.*?\]\]", hide, text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\x00\"“])", hidden)
    out: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        lead = re.match(r"(\x00\d+\x00\s*[.;,]?\s*)+", p)
        if lead and out:  # citations after a full stop belong to the previous sentence
            out[-1] += " " + lead.group(0).strip()
            p = p[lead.end():].strip()
        if p:
            out.append(p)
    return [re.sub(r"\x00(\d+)\x00", lambda m: cites[int(m.group(1))], s) for s in out]


def strip_cites(s: str) -> str:
    return re.sub(r"\[\[.*?\]\]", "", s).strip()


def check_sentence(s: str, index: dict[str, Chunk], game_names: list[str]) -> Result:
    cites = CITE.findall(s)
    plain = strip_cites(s)
    if PROPOSAL.match(plain):
        kind = "proposal"
    elif QUESTION.match(plain):
        kind = "question"
    else:
        kind = "claim"
    if not cites:
        if kind != "claim":
            named = [g for g in game_names if norm(g) in norm(plain)]
            if named:  # proposals/questions that lean on a reference game without evidence
                return Result(s, kind, "warn_uncited_reference", detail=f"names {named[0]} without a quoted citation")
            return Result(s, kind, "n/a")
        if "[[" in s:
            return Result(s, kind, "missing_quote", detail="citation without a quoted span")
        return Result(s, kind, "uncited")
    r = Result(s, kind, "supported", citations=cites)
    chunks = []
    for cid, quote in cites:
        c = index.get(cid)
        if c is None:
            r.status, r.detail = "bad_chunk", f"no chunk {cid}"
            return r
        if norm(quote).strip(" .,;:!?") not in norm(c.text):  # edge punctuation is not content
            r.status, r.detail = "quote_not_found", f"quote not in {cid}"
            return r
        chunks.append(c)
    if kind != "claim":  # proposals/questions may cite; only the quotes are checked
        return r
    evidence = " ".join(c.text for c in chunks)
    ev_norm, ev_nums = norm(evidence), numbers(evidence)
    claim_body = re.sub(r"^[-*]\s*", "", plain)
    missing = numbers(claim_body) - ev_nums
    if missing:
        r.status, r.detail = "number_mismatch", f"numbers not in cited chunks: {sorted(missing)}"
        return r
    for g in game_names:
        if norm(g) in norm(claim_body) and norm(g) not in ev_norm:
            r.status, r.detail = "entity_mismatch", f"'{g}' not in cited chunks"
            return r
    words = set(tokens(claim_body))
    if words:
        ev_words = set(tokens(evidence))
        ov = len(words & ev_words) / len(words)
        if ov < MIN_OVERLAP:
            r.status, r.detail = "low_overlap", f"content-word overlap {ov:.2f} < {MIN_OVERLAP}"
    return r


def check_markdown(md: str, chunks: list[Chunk]) -> list[Result]:
    index = {c.id: c for c in chunks}
    games = sorted({c.game for c in chunks if c.game}, key=len, reverse=True)
    results = []
    for ln, line in enumerate(md.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("<!--") or s.startswith("|") or s.startswith(">"):
            continue
        s = re.sub(r"^([-*]|\d+\.)\s+", "", s)
        for sent in split_sentences(s):
            r = check_sentence(sent, index, games)
            r.line = ln
            results.append(r)
    return results


def summarize(results: list[Result]) -> dict:
    claims = [r for r in results if r.kind == "claim"]
    supported = [r for r in claims if r.status == "supported"]
    by = {}
    for r in claims:
        by[r.status] = by.get(r.status, 0) + 1
    return {
        "claims": len(claims),
        "supported": len(supported),
        "valid_citation_share": round(len(supported) / len(claims), 3) if claims else None,
        "proposals": sum(r.kind == "proposal" for r in results),
        "questions": sum(r.kind == "question" for r in results),
        "claim_status": dict(sorted(by.items())),
        "cited_but_failed": sum(1 for r in claims if r.citations and r.status != "supported"),
        "non_claim_warnings": sum(r.kind != "claim" and r.status not in ("n/a", "supported") for r in results),
    }


def report_markdown(results: list[Result], title: str = "Citation check") -> str:
    s = summarize(results)
    out = [f"# {title}", "",
           f"- Claims (sentences that are not proposals or questions): **{s['claims']}**",
           f"- Claims with valid citations: **{s['supported']}** "
           f"({s['valid_citation_share']:.0%})" if s["claims"] else "- No claims",
           f"- Proposals (design opinion, no citation needed): {s['proposals']}",
           f"- Open questions: {s['questions']}",
           f"- Claim status counts: {s['claim_status']}",
           f"- Proposals/questions flagged (name a reference game without a quoted citation, or a bad quote): "
           f"{s['non_claim_warnings']}", "",
           "## Flagged sentences", ""]
    flagged = [r for r in results if r.kind == "claim" and r.status != "supported"]
    flagged += [r for r in results if r.kind != "claim" and r.status not in ("n/a", "supported")]
    if not flagged:
        out.append("None.")
    for r in flagged:
        out.append(f"- line {r.line} **{r.status}** {('(' + r.detail + ')') if r.detail else ''}: {strip_cites(r.sentence)}")
    return "\n".join(out) + "\n"
