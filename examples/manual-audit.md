# Manual audit of claims the checker marked "supported"

One person read every claim the checker passed in the two committed GDDs
(`ollama-qwen3.5-9b/tidebound` and `ollama-qwen3.5-9b/gearhollow`) against its cited chunk.
Question asked: does the sentence say only what the quoted source says?

| GDD | Supported claims | Faithful | Not faithful |
| --- | --- | --- | --- |
| Tidebound | 7 | 6 | 1 |
| Gearhollow | 5 | 5 | 0 |
| Total | 12 | 11 | 1 |

The one miss (Tidebound, Monetization): "Slay the Spire was priced at $24.99 on the US store
when it released on Jan 23, 2019." The chunk gives the current US price and the release date
separately. It does not say the game cost $24.99 at release. Every number and name in the
sentence is in the chunk, so the lexical checker passes it. This is the same blind spot as the
`inversion` category in the planted-claim benchmark.

Other things noticed while reading (they do not change the counts):
- Several "claims" restate the team's own notes (for example "The team wants no combat and no
  fail state"). They are correctly cited, but they are project constraints, not market facts.
- The 9B model repeats the same notes sentence across sections, so the GDDs are repetitive.
- 13 bullets cite a chunk id with no quote (`[[N-tidebound-7]]`). The checker reports these as
  `missing_quote` even when the sentence is true: the rule is that evidence must be quoted.
