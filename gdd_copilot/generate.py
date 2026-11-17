"""Generate a GDD section by section from a pitch, grounded in retrieved corpus chunks."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Callable

from .corpus import BM25, Chunk

SECTIONS = [
    ("Core loop", "core gameplay loop mechanics run deck build turn combat progression"),
    ("Target player", "players audience review played hours recommended single-player controller"),
    ("Reference games", "developer publisher release date price genres metacritic recommendations"),
    ("Monetization", "price free to play DLC expansion premium"),
    ("Risks", "not recommended difficulty repetitive competition genre crowded"),
    ("Open questions", "unknown scope platform audience"),
]

RULES = """You write one section of a game design document (GDD) in Markdown.
Output only bullet points ("- "), one sentence per bullet, 4 to 7 bullets. No heading.
Every bullet must be exactly one of these three kinds:
1. A FACT about reference games, players or the market. It must end with a citation copied from
   the EVIDENCE list, in this exact form: [[ID: "exact words"]]
   - ID is the evidence id, e.g. S646570-facts.
   - "exact words" is a span of 3 to 20 words copied character for character from that evidence.
   - Only state what the quoted evidence says. Do not compute new numbers or percentages.
   - Name a game only if the cited evidence names it.
2. A design idea or opinion about the NEW game. It must start with "Proposal: " and needs no citation.
3. An unknown that needs research. It must start with "Open question: ".
Never state a fact without a citation. If the evidence does not support it, turn it into an
Open question or drop it."""


# provider -> (OpenAI-compatible base URL, key env var, default model)
PROVIDERS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini-3.5-flash"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat"),
    "openai": (os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), "OPENAI_API_KEY", "gpt-4o-mini"),
    "ollama": (None, None, "qwen3.5:9b"),
}
USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def provider() -> tuple[str, str]:
    """GDD_PROVIDER wins; otherwise the first cloud key found in the environment; else Ollama."""
    name = os.environ.get("GDD_PROVIDER") or next(
        (p for p in ("gemini", "deepseek") if os.environ.get(PROVIDERS[p][1])), "ollama")
    return name, os.environ.get("GDD_MODEL", PROVIDERS[name][2])


def ollama_has(model: str) -> bool:
    """True if a local Ollama answers and has `model` pulled."""
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=3) as r:
            return any(m["name"] == model for m in json.load(r)["models"])
    except (OSError, ValueError, KeyError):
        return False


def resolve_mode() -> str:
    """Demo order: GDD_PROVIDER if set; cloud key; local Ollama with the model; else 'replay'."""
    if os.environ.get("GDD_PROVIDER"):
        return os.environ["GDD_PROVIDER"]
    for p in ("gemini", "deepseek"):
        if os.environ.get(PROVIDERS[p][1]):
            return p
    return "ollama" if ollama_has(os.environ.get("GDD_MODEL", PROVIDERS["ollama"][2])) else "replay"


def replay(folder: Path) -> Callable[[str, str], str]:
    """An llm() that returns recorded responses, keyed like the section cache."""
    def call(system: str, user: str) -> str:
        f = folder / (hashlib.sha256((system + user).encode()).hexdigest()[:16] + ".txt")
        if not f.exists():
            raise KeyError(f"no recorded response {f.name} (pitch, corpus or prompt changed)")
        return f.read_text()
    return call


def call_llm(system: str, user: str) -> str:
    name, model = provider()
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    USAGE["calls"] += 1
    if name != "ollama":
        base, key_env, _ = PROVIDERS[name]
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=json.dumps({"model": model, "messages": msgs, "temperature": 0.2}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ[key_env]})
        with urllib.request.urlopen(req, timeout=300) as r:
            out = json.load(r)
        for k in ("prompt_tokens", "completion_tokens"):
            USAGE[k] += (out.get("usage") or {}).get(k, 0)
        return out["choices"][0]["message"]["content"]
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    body = {"model": model, "messages": msgs, "stream": False, "think": False,
            "options": {"temperature": 0.2, "num_ctx": 8192}}
    req = urllib.request.Request(host + "/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.load(r)["message"]["content"]


def evidence_block(chunks: list[Chunk]) -> str:
    return "\n".join(f"[{c.id}] ({c.source}) {c.text}" for c in chunks)


def section_prompt(pitch: str, section: str, chunks: list[Chunk]) -> str:
    return (f"PITCH:\n{pitch}\n\nSECTION TO WRITE: {section}\n\n"
            f"EVIDENCE (cite these ids only):\n{evidence_block(chunks)}\n")


def retrieve(index: BM25, pitch: str, query: str, pinned: list[Chunk], k: int = 10) -> list[Chunk]:
    hits = index.search(pitch + " " + query, k=k)
    seen, out = set(), []
    for c in pinned + hits:
        if c.id not in seen:
            seen.add(c.id)
            out.append(c)
    return out


def clean(text: str) -> str:
    text = re.sub(r"<(think|thought)>.*?</(think|thought)>", "", text, flags=re.S)
    lines = [l.rstrip() for l in text.strip().splitlines()]
    return "\n".join(l for l in lines if l.strip() and not l.lstrip().startswith("#"))


def with_retry(llm: Callable[[str, str], str], tries: int = 3) -> Callable[[str, str], str]:
    """The local model is often shared; retry timeouts and connection errors with backoff."""
    def call(system: str, user: str) -> str:
        for i in range(tries):
            try:
                return llm(system, user)
            except (TimeoutError, OSError):
                if i == tries - 1:
                    raise
                time.sleep(30 * (i + 1))
    return call


def generate(title: str, pitch: str, chunks: list[Chunk],
             llm: Callable[[str, str], str] = call_llm, cache: Path | None = None) -> str:
    """cache: optional folder; each finished section is stored there and reused on re-run."""
    index = BM25(chunks)
    facts = [c for c in chunks if c.id.endswith("-facts")]
    md = [f"# {title}", "", "## Pitch", "", "> " + " ".join(pitch.split()), ""]
    for section, query in SECTIONS:
        pinned = facts if section in ("Reference games", "Monetization") else []
        ev = retrieve(index, pitch, query, pinned)
        prompt = section_prompt(pitch, section, ev)
        key = cache / (hashlib.sha256((RULES + prompt).encode()).hexdigest()[:16] + ".txt") if cache else None
        if key and key.exists():
            text = key.read_text()
        else:
            text = clean(llm(RULES, prompt))
            if key:
                key.parent.mkdir(parents=True, exist_ok=True)
                key.write_text(text)
        md += [f"## {section}", "", text, ""]
    return "\n".join(md)
