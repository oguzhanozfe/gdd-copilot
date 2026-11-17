"""Offline tests for gdd_copilot (stdlib unittest, no network, no Ollama)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gdd_copilot import bench, check, corpus, generate, render  # noqa: E402
from gdd_copilot.corpus import BM25, Chunk  # noqa: E402

STEAM = ROOT / "corpus" / "steam"
NOTES = ROOT / "corpus" / "notes"

# Small hand-made corpus for deterministic checker tests.
CH = Chunk("S1-facts", "Foo Game (Steam app 1). Developer: Acme. Release date: Jan 5, 2020. "
           "Steam user recommendations: 12,345.", "Steam: Foo Game", "https://example.com/foo", "2026-01-02", "Foo Game")
CH2 = Chunk("S2-facts", "Bar Game (Steam app 2). Developer: Zed.", "Steam: Bar Game",
            "https://example.com/bar", "2026-01-03", "Bar Game")
CHUNKS = [CH, CH2]
INDEX = {c.id: c for c in CHUNKS}
GAMES = ["Foo Game", "Bar Game"]


def cs(s):
    return check.check_sentence(s, INDEX, GAMES)


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chunks = corpus.load(STEAM)

    def test_ids_and_facts(self):
        by = {c.id: c for c in self.chunks}
        self.assertIn("S646570-facts", by)
        self.assertIn("Developer: Mega Crit", by["S646570-facts"].text)

    def test_notes_one_chunk_per_bullet(self):
        path = NOTES / "gearhollow.md"
        bullets = [l for l in path.read_text().splitlines() if l.startswith("- ")]
        out = [c for c in corpus.notes_chunks(path) if c.text in {b[2:] for b in bullets}]
        self.assertEqual(len(out), len(bullets))
        ids = [c.id for c in corpus.notes_chunks(path)]
        self.assertEqual(ids, [f"N-gearhollow-{i}" for i in range(1, len(ids) + 1)])

    def test_bm25_ranks_relevant_first(self):
        top = BM25(self.chunks).search("relics deck spire", k=3)
        self.assertTrue(top[0].id.startswith("S646570-"), top[0].id)


class SplitTests(unittest.TestCase):
    def test_citation_after_full_stop_stays_attached(self):
        s = 'Foo is good. Bar is bad. [[S1-facts: "Developer: Acme"]]'
        self.assertEqual(check.split_sentences(s), ["Foo is good.", 'Bar is bad. [[S1-facts: "Developer: Acme"]]'])

    def test_mid_text_citation_goes_to_previous_sentence(self):
        s = 'Foo is good. [[S1-facts: "Developer: Acme"]] Bar is bad.'
        self.assertEqual(check.split_sentences(s), ['Foo is good. [[S1-facts: "Developer: Acme"]]', "Bar is bad."])

    def test_no_split_inside_quote(self):
        s = 'Foo ships. Bar too. [[S1-facts: "Foo Game (Steam app 1). Developer: Acme."]]'
        out = check.split_sentences(s)
        self.assertEqual(len(out), 2)
        self.assertIn("Foo Game (Steam app 1). Developer: Acme.", out[1])


class CheckTests(unittest.TestCase):
    def test_supported(self):
        r = cs('Foo Game was developed by Acme. [[S1-facts: "Developer: Acme"]]')
        self.assertEqual(r.status, "supported", r.detail)

    def test_uncited(self):
        self.assertEqual(cs("Foo Game sold well.").status, "uncited")

    def test_citation_without_quote(self):
        self.assertEqual(cs("Foo Game sold well. [[S1-facts]]").status, "missing_quote")

    def test_quote_edge_punctuation_ignored(self):
        self.assertEqual(cs('Foo Game by Acme. [[S1-facts: "Developer: Acme."]]').status, "supported")

    def test_bad_chunk(self):
        self.assertEqual(cs('Foo Game by Acme. [[S1-nope: "Developer: Acme"]]').status, "bad_chunk")

    def test_quote_not_found(self):
        self.assertEqual(cs('Foo Game by Acme. [[S1-facts: "Developer: Nobody"]]').status, "quote_not_found")

    def test_quote_normalisation(self):
        for q in ['DEVELOPER:   ACME', "Developer:\tAcme"]:
            self.assertEqual(cs(f'Foo Game by Acme. [[S1-facts: "{q}"]]').status, "supported", q)
        curly = '[[S1-facts: “Developer: Acme”]]'
        self.assertEqual(cs(f"Foo Game by Acme. {curly}").status, "supported")
        dash = Chunk("S3-x", "Foo Game – it’s a deck builder", "s", "u", "d", "Foo Game")
        r = check.check_sentence('Foo Game is a deck builder. [[S3-x: "foo game - it\'s a deck builder"]]',
                                 {"S3-x": dash}, ["Foo Game"])
        self.assertEqual(r.status, "supported", r.detail)

    def test_number_mismatch(self):
        r = cs('Foo Game has 99,999 Steam user recommendations. [[S1-facts: "Steam user recommendations: 12,345"]]')
        self.assertEqual(r.status, "number_mismatch")

    def test_entity_mismatch(self):
        r = cs('Bar Game was developed by Acme. [[S1-facts: "Developer: Acme"]]')
        self.assertEqual(r.status, "entity_mismatch")

    def test_low_overlap(self):
        r = cs('Players build custom levels with a map editor sandbox. [[S1-facts: "Developer: Acme"]]')
        self.assertEqual(r.status, "low_overlap")

    def test_proposal_and_question_not_claims(self):
        for s, kind in [("Proposal: add a gamepad mode.", "proposal"), ("Open question: how big is the team?", "question")]:
            r = cs(s)
            self.assertEqual((r.kind, r.status), (kind, "n/a"))

    def test_proposal_naming_reference_game_warns(self):
        r = cs("Proposal: price it like Foo Game.")
        self.assertEqual((r.kind, r.status), ("proposal", "warn_uncited_reference"))
        r = cs('Proposal: price it like Foo Game. [[S1-facts: "Developer: Acme"]]')
        self.assertEqual(r.status, "supported")

    def test_markdown_skips_headings_and_blockquotes(self):
        md = "# Title\n\n## Pitch\n\n> A pitch sentence with no citation.\n\n- Proposal: do it.\n- Uncited claim here.\n"
        res = check.check_markdown(md, CHUNKS)
        self.assertEqual([r.sentence for r in res], ["Proposal: do it.", "Uncited claim here."])
        self.assertEqual([r.line for r in res], [7, 8])

    def test_summarize(self):
        md = "\n".join([
            '- Foo Game was developed by Acme. [[S1-facts: "Developer: Acme"]]',
            "- Foo Game sold well.",
            "- Proposal: ship it.",
            "- Open question: why?",
        ])
        s = check.summarize(check.check_markdown(md, CHUNKS))
        self.assertEqual((s["claims"], s["supported"], s["proposals"], s["questions"]), (2, 1, 1, 1))
        self.assertEqual(s["valid_citation_share"], 0.5)
        self.assertEqual(s["claim_status"], {"supported": 1, "uncited": 1})
        self.assertEqual(s["cited_but_failed"], 0)
        self.assertIsNone(check.summarize([])["valid_citation_share"])


class GenerateTests(unittest.TestCase):
    def test_generate_with_fake_llm(self):
        chunks = corpus.load(STEAM)
        calls = []

        def fake(system, user):
            calls.append((system, user))
            return '- Slay the Spire has a deck. [[S646570-facts: "Developer: Mega Crit"]]\n- Proposal: be nice.'

        md = generate.generate("My GDD", "A  deck\nbuilder with relics.", chunks, llm=fake)
        for sec, _ in generate.SECTIONS:
            self.assertIn(f"## {sec}", md)
        self.assertIn("> A deck builder with relics.", md)
        self.assertEqual(len(calls), len(generate.SECTIONS))
        for _, user in calls:
            self.assertIn("EVIDENCE", user)
            self.assertRegex(user, r"\[S\d+-\w+\]")
        self.assertIn("[S646570-facts]", calls[2][1])  # Reference games pins facts chunks


class RenderTests(unittest.TestCase):
    MD = ("# T\n\n## S\n\n"
          '- Foo Game by Acme. [[S1-facts: "Developer: Acme"]]\n'
          '- Foo Game released then. [[S1-facts: "Release date: Jan 5, 2020"]]\n'
          '- Bar Game by Zed. [[S2-facts: "Developer: Zed"]] Again. [[S1-facts: "Developer: Acme"]]\n')

    def test_markdown_numbering_and_sources(self):
        out = render.to_markdown(self.MD, CHUNKS)
        self.assertIn("Foo Game by Acme. [1]", out)
        self.assertIn("released then. [2]", out)
        self.assertIn("Zed. [3] Again. [1]", out)
        self.assertIn("## Sources", out)
        self.assertIn("<https://example.com/foo>, retrieved 2026-01-02", out)
        self.assertIn("3. `S2-facts`", out)

    def test_html_escapes_flags_and_sources(self):
        md = ('- Foo Game <script>alert(1)</script> by Acme. [[S1-facts: "Developer: Acme"]]\n'
              "- Uncited claim here.\n")
        res = check.check_markdown(md, CHUNKS)
        h = render.to_html(md, CHUNKS, res, "T <b>")
        self.assertNotIn("<script>", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertIn("<title>T &lt;b&gt;</title>", h)
        self.assertIn('class="flag"', h)
        self.assertIn("[uncited]", h)
        self.assertIn('<ol class="sources">', h)
        self.assertIn('<a href="https://example.com/foo">', h)


class BenchTests(unittest.TestCase):
    GDD = "# T\n\n## A\n\n- Proposal: one.\n- Proposal: two.\n\n## B\n\n- Proposal: three.\n"

    def test_bench(self):
        chunks, snaps = corpus.load(STEAM), bench.load_snaps(STEAM)
        r = bench.run(self.GDD, chunks, snaps)
        self.assertEqual(r["controls_false_flagged"], 0)
        for cat in ("uncited", "fake_quote", "bad_chunk", "number_mismatch", "entity_mismatch", "off_topic"):
            self.assertEqual(r["by_category"][cat]["rate"], 1.0, cat)
        for cat in ("inversion", "wrong_company"):  # known misses: existence only
            self.assertIn(cat, r["by_category"])


class DemoTests(unittest.TestCase):
    def test_keyless_resolves_to_replay(self):
        import os
        from unittest import mock
        env = {k: v for k, v in os.environ.items() if k not in ("GDD_PROVIDER", "GEMINI_API_KEY", "DEEPSEEK_API_KEY")}
        env["OLLAMA_HOST"] = "http://127.0.0.1:9"
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(generate.resolve_mode(), "replay")

    def test_replay_rebuilds_committed_example(self):
        import os
        from contextlib import redirect_stdout
        from io import StringIO
        from tempfile import TemporaryDirectory
        from unittest import mock
        from gdd_copilot import __main__ as cli
        os.chdir(ROOT)
        with TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"GDD_PROVIDER": "replay"}), \
                redirect_stdout(StringIO()):
            cli.main(["demo", "--out", tmp])
            for name in ("tidebound", "gearhollow"):
                got = (Path(tmp) / name / "gdd.raw.md").read_text()
                want = (cli.RECORDED / name / "gdd.raw.md").read_text()
                self.assertEqual(got, want)
            self.assertIn("gdd.html", (Path(tmp) / "index.html").read_text())


if __name__ == "__main__":
    unittest.main()
