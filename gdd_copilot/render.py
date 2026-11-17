"""Render a checked GDD to readable Markdown (numbered sources) and printable HTML."""
from __future__ import annotations

import html
import re

from .check import CITE, Result, split_sentences, strip_cites, summarize
from .corpus import Chunk

CSS = """
:root{--fg:#1b1b1b;--muted:#5d5d5d;--bad:#b3261e;--badbg:#fdecea;--line:#ddd}
body{font:16px/1.55 Georgia,serif;color:var(--fg);background:#fff;max-width:46rem;margin:2rem auto;padding:0 1rem}
h1{font-size:1.9rem;margin-bottom:.2rem}h2{border-bottom:1px solid var(--line);padding-bottom:.2rem;margin-top:2rem}
blockquote{color:var(--muted);border-left:3px solid var(--line);margin:0;padding-left:1rem}
sup a{text-decoration:none;font-size:.75em}.proposal{color:#244b8a}.question{color:#6b4a00}
.flag{background:var(--badbg);border-bottom:2px solid var(--bad)}.flag small{color:var(--bad);font-family:sans-serif}
ol.sources{font-size:.85rem;color:var(--muted)}ol.sources q{color:var(--fg)}
.ok{background:#e8f5e9;border-bottom:2px solid #2e7d32}
.legend,.summary{font:.8rem sans-serif;color:var(--muted)}.summary{font-size:.9rem;color:var(--fg)}
@media print{body{margin:0;max-width:none;font-size:11pt}a{color:inherit}h2{break-after:avoid}li{break-inside:avoid}}
"""


def _numbering(md: str) -> dict[tuple[str, str], int]:
    nums: dict[tuple[str, str], int] = {}
    for cid, quote in CITE.findall(md):
        nums.setdefault((cid, quote), len(nums) + 1)
    return nums


def to_markdown(md: str, chunks: list[Chunk]) -> str:
    """Replace inline [[id: "quote"]] with [n] and append a numbered source list."""
    index = {c.id: c for c in chunks}
    nums = _numbering(md)
    body = CITE.sub(lambda m: f"[{nums[(m.group(1), m.group(2))]}]", md)
    out = [body.rstrip(), "", "## Sources", ""]
    for (cid, quote), n in nums.items():
        c = index.get(cid)
        where = f"{c.source}, <{c.url}>, retrieved {c.retrieved}" if c else "unknown source id"
        out.append(f"{n}. `{cid}`: \"{quote}\" ({where})")
    return "\n".join(out) + "\n"


def to_html(md: str, chunks: list[Chunk], results: list[Result], title: str) -> str:
    index = {c.id: c for c in chunks}
    nums = _numbering(md)
    flagged = {(r.line, r.sentence): r for r in results if r.kind == "claim" and r.status != "supported"}
    verified = {(r.line, r.sentence) for r in results if r.kind == "claim" and r.status == "supported"}
    esc = html.escape

    def sentence_html(s: str, line: int) -> str:
        def cite(m):
            n = nums[(m.group(1), m.group(2))]
            return f'<sup><a href="#src{n}" title="{esc(m.group(2))}">[{n}]</a></sup>'
        parts, last = [], 0
        for m in CITE.finditer(s):
            parts.append(esc(s[last:m.start()]) + cite(m))
            last = m.end()
        parts.append(esc(s[last:]))
        inner = "".join(parts)
        low = s.lower()
        if (line, s) in verified:
            return f'<span class="ok" title="citation verified">{inner}</span>'
        if (line, s) in flagged:
            r = flagged[(line, s)]
            return f'<span class="flag">{inner} <small>[{esc(r.status)}]</small></span>'
        if low.startswith("proposal:"):
            return f'<span class="proposal">{inner}</span>'
        if low.startswith("open question:"):
            return f'<span class="question">{inner}</span>'
        return inner

    body, in_list = [], False
    for ln, line in enumerate(md.splitlines(), 1):
        s = line.strip()
        is_item = bool(re.match(r"^([-*]|\d+\.)\s+", s))
        if in_list and not is_item:
            body.append("</ul>")
            in_list = False
        if not s:
            continue
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            body.append(f"<h{level}>{esc(s.lstrip('#').strip())}</h{level}>")
        elif s.startswith("<!--"):  # provenance footer: which model wrote this GDD, and when
            body.append(f'<p class="legend">{esc(s.strip("<!-> ").capitalize())}.</p>')
        elif s.startswith(">"):
            body.append(f"<blockquote>{esc(s[1:].strip())}</blockquote>")
        else:
            text = re.sub(r"^([-*]|\d+\.)\s+", "", s)
            rendered = " ".join(sentence_html(x, ln) for x in split_sentences(text))
            if is_item:
                if not in_list:
                    body.append("<ul>")
                    in_list = True
                body.append(f"<li>{rendered}</li>")
            else:
                body.append(f"<p>{rendered}</p>")
    if in_list:
        body.append("</ul>")
    src = []
    for (cid, quote), n in nums.items():
        c = index.get(cid)
        meta = (f'{esc(c.source)}, <a href="{esc(c.url)}">{esc(c.url)}</a>, retrieved {esc(c.retrieved)}'
                if c and c.url.startswith("http") else esc(c.source) if c else "unknown source id")
        src.append(f'<li id="src{n}"><code>{esc(cid)}</code>: <q>{esc(quote)}</q> ({meta})</li>')
    legend = ('<p class="legend">Green = claim whose quoted citation the checker verified. '
              'Red = claim it could not verify. Blue = proposal (design opinion). Brown = open question.</p>')
    sm = summarize(results)
    share = f" ({sm['valid_citation_share']:.0%})" if sm["claims"] else ""
    summary = (f'<p class="summary"><b>Citation check:</b> {sm["supported"]} of {sm["claims"]} claims have '
               f'valid citations{share}; {sm["proposals"]} proposals, {sm["questions"]} open questions, '
               f'{sm["non_claim_warnings"]} proposals/questions name a reference game without a quote.</p>')
    report = ['<h2>Citation check</h2>', summary, "<ul>"]
    for r in results:
        if (r.kind == "claim" and r.status != "supported") or (r.kind != "claim" and r.status not in ("n/a", "supported")):
            report.append(f"<li>line {r.line} <b>{esc(r.status)}</b> {esc(r.detail)}: {esc(strip_cites(r.sentence))}</li>")
    report.append("</ul>")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{esc(title)}</title><style>{CSS}</style></head><body>"
            + legend + summary + "\n".join(body)
            + '<h2>Sources</h2><ol class="sources">' + "\n".join(src) + "</ol>"
            + "\n".join(report) + "</body></html>\n")
