"""Research pipeline end to end with a scripted model and fake web. No network."""

import re

from llamawatch.research import pipeline as pl
from llamawatch.research.llm import Model, ModelSpec, loose_json, sections
from llamawatch.research.store import RunStore
from llamawatch.research.web import Page, SearchHit

PAGES = {
    "https://maker.example.gov/spec": "Spec sheet.\n\nThe Widget 9 holds 128 GB of memory and costs 1,999 pounds at launch.\n\n" + "Filler text about the product line. " * 30,
    "https://review.example.org/test": "Review.\n\nIn our test the Widget 9 ran the 120B model at 46 tokens per second with the Vulkan backend.\n\n" + "More review text here. " * 30,
    "https://forum.example.net/threads/w9": "Thread.\n\nMine only managed 30 tokens per second on the 120B model, not the 46 tokens per second the review says.\n\n" + "Forum chatter. " * 40,
}


class FakeSearcher:
    health = []

    def search(self, q, n=8):
        return [SearchHit(u, u.split("/")[2], "Widget 9 120B tokens") for u in PAGES]


class FakeReader:
    stats = {}

    def read(self, url, fast=False):
        return Page(url, True, PAGES[url], title=url.split("/")[2], retrieval="fetch")

    def close(self):
        self.closed = True


SCOPE = """## Restated question
How fast and how expensive is the Widget 9 for 120B models?
## Report type
buying
## Perspectives
- home user: speed
## Sub-questions
### T1: How fast does the Widget 9 run 120B models?
Complete answer: tokens per second with setup
Searches: widget 9 120b speed | widget 9 benchmark
### T2: What does the Widget 9 cost?
Complete answer: price and memory
Searches: widget 9 price
## Known techniques
- Vulkan backend
## Landscape
- none
## Done means
- speed
- price
"""


def fake_chat(self, prompt, system="", max_tokens=0, temperature=0, stage=""):
    self.usage.calls += 1
    if stage == "scope":
        return SCOPE
    if stage == "extract":
        if "maker.example.gov" in prompt:
            return ("RELEVANT: yes\nPUBLISHED: 2026-05-01\nTYPE: primary\nQUOTES:\n"
                    "> The Widget 9 holds 128 GB of memory and costs 1,999 pounds at launch.\n- shows: price and memory\n"
                    "> The Widget 9 is the fastest machine ever built by anyone.\n- shows: invented quote")
        if "review.example.org" in prompt:
            return ("RELEVANT: yes\nPUBLISHED: 2026-06-01\nTYPE: secondary\nQUOTES:\n"
                    "> In our test the Widget 9 ran the 120B model at 46 tokens per second with the Vulkan backend.\n- shows: 46 tok/s Vulkan")
        return ("RELEVANT: yes\nPUBLISHED: unknown\nTYPE: primary\nQUOTES:\n"
                "> Mine only managed 30 tokens per second on the 120B model, not the 46 tokens per second the review says.\n- shows: one user saw 30 tok/s")
    if stage == "reflect":
        return "MISSING:\n- nothing\nDONE: yes\nSEARCHES:\n- none"
    if stage == "claims":
        ids = re.findall(r"- (E\d+) \(([^,]+)", prompt)
        out = []
        for eid, site in ids:
            if "maker" in site:
                out.append(f"- The Widget 9 has 128 GB of memory and costs 1,999 pounds at launch [{eid}]")
                out.append(f"- The Widget 9 costs 2,499 pounds [{eid}]")
            elif "review" in site:
                out.append(f"- The Widget 9 runs a 120B model at 46 tokens per second on Vulkan [{eid}]")
            else:
                out.append(f"- One user measured 30 tokens per second on the 120B model [{eid}] {{against}}")
        return "\n".join(out)
    if stage == "verify":
        blocks = []
        for cid, text in re.findall(r"^(C\d+) \([a-z]+\): (.*)$", prompt, re.M):
            if "2,499" in text:
                blocks.append(f"{cid}: none\nFix: drop\nWhy: price not in quote\nCounter: widget 9 price")
            elif "46 tokens" in text:
                blocks.append(f"{cid}: partial\nFix: One review measured 46 tokens per second on a 120B model with Vulkan\nWhy: single test\nCounter: widget 9 slow")
            else:
                blocks.append(f"{cid}: full\nFix: none\nWhy: matches\nCounter: widget 9 problems")
        return "\n\n".join(blocks)
    if stage == "counter":
        return "VERDICT: unrelated\nQUOTE:\n> nothing\nWHY: n/a"
    if stage == "gaps":
        return "MISSING:\n- nothing"
    if stage == "outline":
        return "## The Widget 9 runs 120B models at 46 tokens per second\nC3\n## It costs 1,999 pounds with 128 GB\nC1"
    if stage in ("write", "rewrite"):
        if "Write the opening" in prompt:
            return ("# The Widget 9 is a sound 120B buy\n\n## Summary\nThe Widget 9 runs 120B models at 46 tokens per second [2] "
                    "and costs 1,999 pounds [1]. It costs 2,499 pounds in shops.\n\n## Key numbers\n| What | Number | Source |\n|---|---|---|\n"
                    "| Price at launch | 1,999 pounds | [1] |\n| Speed, 120B, Vulkan | 46 tok/s | [2] |")
        if "## The case against" in prompt:
            return "## The case against\nOne user measured 30 tokens per second [3].\n\n## Risks and unknowns\n- Only one review."
        if "Recommendations" in prompt:
            return "## Recommendations\n1. Check the price before buying [1]."
        if "failed an automatic check" in prompt:
            return "The Widget 9 runs 120B models at 46 tokens per second [2] and costs 1,999 pounds [1]."
        return "One review measured 46 tokens per second on a 120B model with Vulkan [2]. It has 128 GB [1]."
    return ""


def make_run(tmp_path, monkeypatch, depth="quick"):
    monkeypatch.setattr(Model, "chat", fake_chat)
    m = Model(ModelSpec(name="fake", url="http://x", model="fake"))
    store = RunStore(tmp_path)
    return pl.ResearchRun("Is the Widget 9 good for 120B models?", depth, m, store,
                          FakeSearcher(), FakeReader(), today="2026-10-07"), store


def test_full_run(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch, depth="full")
    report = run.run()
    assert store.meta["status"] == "done"
    # invented quote thrown away by the app
    assert run.stats["quotes_rejected"] == 1
    assert all("fastest machine" not in e.quote for e in store.evidence.values())
    checks = {c.text: c.check for c in store.claims.values()}
    assert checks["The Widget 9 costs 2,499 pounds."] == "dropped"
    assert any(v == "corrected" for v in checks.values())
    # report shape the page expects
    for h in ("## Summary", "## Key numbers", "## The case against", "## Risks and unknowns",
              "## Recommendations", "## How this was researched", "## Verification log", "## Sources"):
        assert h in report, h
    assert report.startswith("# The Widget 9 is a sound 120B buy")
    # dropped number caught and rewritten out of the summary
    summary = report.split("## Summary")[1].split("## Key numbers")[0]
    assert "2,499" not in summary
    # pages are read side by side, so the source number depends on which page answered first
    assert re.search(r"^\d\. .*https://maker\.example\.gov/spec\. 2026-05-01\. Read .*\. Primary\.$", report, re.M)
    assert store.meta["citation_precision"] == 1.0
    assert run.reader.closed


def test_strength_counts_sites(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch)
    a = store.add_page("https://a.example.org/x", "t", kind="secondary")
    b = store.add_page("https://b.example.org/y", "t", kind="secondary")
    f = store.add_page("https://forum.example.net/z", "t", kind="forum")
    e1, e2, e3 = (store.add_evidence(p.id, "q") for p in (a, b, f))
    C = pl.Claim
    assert run.strength(C(id="", text="", evidence=[e1.id, e2.id])) == "strong"
    assert run.strength(C(id="", text="", evidence=[e1.id])) == "moderate"
    assert run.strength(C(id="", text="", evidence=[e3.id])) == "weak"
    assert run.strength(C(id="", text="", evidence=[e1.id, e2.id], estimate=True)) == "moderate"


def test_fit_keeps_relevant_parts(tmp_path, monkeypatch):
    run, _ = make_run(tmp_path, monkeypatch)
    text = "Intro paragraph.\n\n" + "\n\n".join(f"Unrelated block {i}. " * 60 for i in range(20)) + \
           "\n\nThe widget speed is 46 tokens per second.\n\n" + "Tail. " * 300
    out = run.fit(text, ["widget", "speed"], 6000)
    assert len(out) <= 6500 and "46 tokens" in out and out.startswith("Intro")


def test_numbers_and_grounding():
    assert pl.numbers("costs 1,999 pounds, 46 tok/s, 3.5x, 2 boxes") == {"1999", "46", "3.5"}
    assert pl.grounded({"46", "53"}, "ran at 46 tok/s") == {"53"}


def test_output_readers():
    md = "intro\n## Summary\nhello\n## Key numbers\n| a |"
    assert sections(md)["summary"] == "hello"
    assert loose_json('Sure:\n```json\n{"a": [1, 2,],}\n```') == {"a": [1, 2]}


def test_drop_bad_refs_removes_missing_sources_keeps_years():
    from llamawatch.research.pipeline import _drop_bad_refs
    out = _drop_bad_refs("a GPU [14, 15]. b [6, 7, 8]. c [3]. d [2025].", 7)
    assert out == "a GPU. b [6][7]. c [3]. d [2025]."


def test_strip_heading_drops_repeated_plain_heading():
    from llamawatch.research.pipeline import _strip_heading
    assert _strip_heading("Who else is doing this\n\nBody.", "Who else is doing this") == "Body."


def test_style_tidy_and_problems():
    from llamawatch.research.style import style_problems, tidy
    t = tidy("In order to run it — fast — you need 600W [2]. It is worth noting that prices vary [3].")
    assert t == "To run it, fast, you need 600W [2]. Prices vary [3]."
    assert style_problems("The test harness runs in landscape mode.") == []
    p = style_problems("This pivotal card is not just fast, but cheap.")
    assert any("pivotal" in x for x in p) and any("not just" in x for x in p)


def test_trust_size_class_and_grade():
    from llamawatch.research.trust import grade, size_class
    assert size_class("Qwen3.6-35B-A3B (local)") == "small"      # sqrt(35*3) ~ 10
    assert size_class("Qwen3-32B") == "medium"
    assert size_class("Llama-3.1-405B-Instruct") == "large"
    assert size_class("gpt-5.4") == "unknown"
    assert size_class("gpt-5.4", paid=True) == "large"
    assert size_class("gpt-5.4", override="large") == "large"
    weak = grade({"confirmed": 5, "weakened": 3, "dropped": 2}, {"pages_read": 4, "pages_blocked": 9},
                 ["secondary"] * 3, 0.7, 9, 5, "small")
    assert weak["level"] == "weak" and len(weak["reasons"]) >= 5
    good = grade({"confirmed": 30, "corrected": 2}, {"pages_read": 30, "pages_blocked": 3},
                 ["primary"] * 4 + ["secondary"] * 8, 0.98, 0, 1, "large")
    assert good["level"] == "good"


class FakeReranker:
    """Scores a passage by how many query words it contains."""
    covered = -5.0

    def __init__(self):
        self.calls = 0

    def scores(self, query, docs):
        self.calls += 1
        q = set(re.findall(r"\w+", query.lower()))
        return [len(q & set(re.findall(r"\w+", d.lower()))) - 6.0 for d in docs]


def test_run_with_scan_reranker_and_limits(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch, depth="full")   # Quick skips the outline these check
    run.reranker = FakeReranker()
    seen = {}
    real = fake_chat

    def chat(self, prompt, **kw):
        if kw.get("stage") == "scope":
            seen["scope"] = prompt
            return SCOPE.replace("## Perspectives", "## Limits\n- under 1,500 pounds\n## Perspectives")
        if kw.get("stage") == "write" and "Section heading:" in prompt:
            seen["section"] = prompt
        return real(self, prompt, **kw)
    monkeypatch.setattr(Model, "chat", chat)
    report = run.run()
    assert "maker.example.gov" in seen["scope"]                 # planner saw the web scan
    assert "under 1,500 pounds" in seen["section"]               # limits reach the writer
    assert "- It costs 1,999 pounds with 128 GB" in seen["section"]   # writer sees the whole outline
    assert run.reranker.calls > 0 and run.stats.get("reranked")
    assert store.meta["limits"] == ["under 1,500 pounds"]
    # the report never uses 1,500, so the limit is reported as not addressed
    assert any("1,500" in u for u in store.meta["uncovered"])
    assert "- The report never addresses the limit: under 1,500 pounds" in report
    assert store.meta["write_mode"] == "section by section"            # model size unknown


def test_one_shot_body_splits_and_backfills(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch, depth="full")   # Quick skips the outline these check
    run.opts["one_shot"] = True
    calls = []
    real = fake_chat

    def chat(self, prompt, **kw):
        if "Write the body of a research report in one go" in prompt:
            calls.append("body")
            # renames the second heading, so that one is written on its own
            return ("## The Widget 9 runs 120B models at 46 tokens per second\n"
                    "One review measured 46 tokens per second on a 120B model with Vulkan [2]. " * 3
                    + "\n\n## Price\nIt costs 1,999 pounds [1].")
        if "Section heading:" in prompt:
            calls.append("section")
        return real(self, prompt, **kw)
    monkeypatch.setattr(Model, "chat", chat)
    report = run.run()
    assert calls.count("body") == 1 and calls.count("section") == 1
    assert store.meta["write_mode"] == "in one go"
    assert report.index("## The Widget 9 runs 120B") < report.index("## It costs 1,999 pounds")
    assert "The report body was written in one go." in report


def test_one_shot_auto_follows_model_size(tmp_path, monkeypatch):
    run, _ = make_run(tmp_path, monkeypatch)
    lines = {"C1": "- C1 [1] (strong): x"}
    assert not run._one_shot([("A", ["C1"])], lines)            # unknown size
    run.model.spec.size = "large"
    assert run._one_shot([("A", ["C1"])], lines)
    run.model.spec.price_in = 1.0
    run.model.spec.size = ""
    assert run._one_shot([("A", ["C1"])], lines)                # paid with no size counts as large


def test_coverage_needs_reranker(tmp_path, monkeypatch):
    run, _ = make_run(tmp_path, monkeypatch)
    run.plan["done"] = ["price in pounds", "noise level in decibels under load"]
    claims = [pl.Claim(id="C1", text="The Widget 9 costs 1,999 pounds, a fair price.", evidence=[])]
    assert run.coverage(claims) == []                            # no reranker: no guessing
    run.reranker = FakeReranker()
    assert run.coverage(claims) == ["no evidence found for: noise level in decibels under load"]


def test_mentions_limits_by_number():
    assert pl._mentions("budget under £3,000", "everything here costs under 3,000 pounds")
    assert not pl._mentions("budget under £3,000", "it costs 4,200 pounds")
    assert pl._mentions("must be quiet", "anything")             # no number: left to the model


def test_reranker_client(monkeypatch):
    from llamawatch.research import rerank as rr

    class R:
        def __init__(self, ok):
            self.ok = ok

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError("down")

        def json(self):
            return {"results": [{"index": 1, "relevance_score": 2.0}, {"index": 0, "relevance_score": -3.0}]}
    assert rr.rerank_url("http://h:8002") == "http://h:8002/v1/rerank"
    assert rr.rerank_url("http://h:8002/v1") == "http://h:8002/v1/rerank"
    assert rr.Reranker.from_config({}) is None
    r = rr.Reranker("http://h:8002")
    monkeypatch.setattr(rr.httpx, "post", lambda *a, **k: R(True))
    assert r.scores("q", ["a", "b"]) == [-3.0, 2.0]
    monkeypatch.setattr(rr.httpx, "post", lambda *a, **k: R(False))
    for _ in range(3):
        assert r.scores("q", ["a"]) is None
    assert not r.live                                            # switched off after 3 failures


def test_eval_repeat_counter():
    from llamawatch.research.evaluate import repeated
    same = "The Widget 9 runs the large model at forty six tokens per second on Vulkan."
    rep = f"# T\n\n## Summary\n{same}\n\n## A\n{same}\n\n## B\n{same}\n\n## How this was researched\n{same}\n"
    assert repeated(rep) == 1                     # A and B; summary and app sections ignored
    assert repeated("# T\n\n## A\nShort one.\n\n## B\nShort one.\n") == 0


def test_limits_ignore_dates_and_none():
    assert pl._mentions("Date: 2026-10-07", "no dates here")
    assert pl._mentions("released after 2025", "nothing")
    assert not pl._mentions("under £3,000 in 2026", "costs 4,000")


def test_two_levels_and_old_names(tmp_path, monkeypatch):
    assert set(pl.DEPTHS) == {"quick", "full"}
    assert all(d["about"] and d["time"] for d in pl.DEPTHS.values())
    assert "starting point" in pl.DEPTH_NOTE
    for old in ("standard", "deep", "Deep", "nonsense"):
        run = pl.ResearchRun("q?", old, Model(ModelSpec(name="f", url="http://x", model="f")),
                             RunStore(tmp_path / old), FakeSearcher(), FakeReader())
        assert run.depth == "full"


def test_quick_is_fast(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch)
    fast, stages = [], []
    real_read = FakeReader.read

    def read(self, url, fast_=False, **kw):
        fast.append(kw.get("fast", fast_))
        return real_read(self, url)
    monkeypatch.setattr(FakeReader, "read", lambda self, url, fast=False: read(self, url, fast))

    def chat(self, prompt, **kw):
        stages.append(kw.get("stage"))
        return fake_chat(self, prompt, **kw)
    monkeypatch.setattr(Model, "chat", chat)
    run.run()
    assert fast and all(fast)                         # short timeouts, no browser
    assert "counter" not in stages and "gaps" not in stages
    assert stages.count("rewrite") <= 1               # one fix for the whole report
    assert run.stats["counter_checks"] == 0


def test_full_counter_checks_and_rewrites(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch)
    run.depth, run.d = "full", pl.DEPTHS["full"]
    stages = []

    def chat(self, prompt, **kw):
        stages.append(kw.get("stage"))
        return fake_chat(self, prompt, **kw)
    monkeypatch.setattr(Model, "chat", chat)
    run.run()
    assert run.stats["counter_checks"] > 0
    assert store.meta["status"] == "done"


def test_clean_queries_drops_prompt_echoes():
    got = pl.clean_queries(['Search 1: "widget 9 benchmark 120B', "a short web search to fill it",
                            "A new short web search on widget 9 price UK",
                            "a new short web search, different from those already done",
                            "Search for widget 9 reviews", "widget", "widget 9 benchmark 120B"])
    assert got == ["widget 9 benchmark 120B", "widget 9 price UK", "widget 9 reviews"]


def test_no_evidence_writes_no_report(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch)
    stages = []

    def chat(self, prompt, **kw):
        stages.append(kw.get("stage"))
        if kw.get("stage") == "extract":
            return "nothing relevant"
        return fake_chat(self, prompt, **kw)
    monkeypatch.setattr(Model, "chat", chat)
    report = run.run()
    assert store.meta["status"] == "no evidence"
    assert report.startswith("# No answer found") and "write" not in stages


def test_quick_skips_outline_and_runs_subquestions_together(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch)
    asked, widths = [], []
    real, par = fake_chat, run._par

    def chat(self, prompt, **kw):
        asked.append(prompt[:60])
        return real(self, prompt, **kw)
    monkeypatch.setattr(Model, "chat", chat)
    run._par = lambda fn, items, workers=None: widths.append(workers) or par(fn, items, workers)
    run.workers = 1
    report = run.run()
    assert store.meta["status"] == "done" and "## Sources" in report
    assert not any(a.startswith("Plan the body of a research report") for a in asked)
    assert widths[0] == len(run.tasks) > 1


def test_quick_skips_recommendations(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch)
    report = run.run()
    assert store.meta["status"] == "done"
    assert "## Summary" in report and "## The case against" in report
    assert "## Recommendations" not in report


def test_claims_see_evidence_in_search_order(tmp_path, monkeypatch):
    # pages are read side by side; the claim writer must see them in search order, not finish order
    run, store = make_run(tmp_path, monkeypatch)
    late = store.add_page("https://b.example.org/later", "text")
    early = store.add_page("https://a.example.org/first", "text")
    run.rank = {early.url: 0, late.url: 1}
    store.add_evidence(late.id, "second ranked quote", "second", task="T1")
    store.add_evidence(early.id, "first ranked quote", "first", task="T1")
    seen = []
    monkeypatch.setattr(Model, "chat", lambda self, prompt, **kw: seen.append(prompt) or "")
    run.build_claims([pl.Task(id="T1", question="q")])
    text = next(p for p in seen if "first ranked" in p)
    assert text.index("a.example.org") < text.index("b.example.org")


def test_garbled_spots_junk_but_not_tables():
    assert pl.garbled("/" * 200)
    assert pl.garbled("## Summary\n" + "." * 40)
    assert pl.garbled("[1] [2] [3] " * 30)                   # hardly any letters
    assert not pl.garbled("")
    assert not pl.garbled("| What | Number |\n|" + "-" * 40 + "|" + "-" * 40 + "|\n| Price | 1,999 pounds |")
    assert not pl.garbled("The Widget 9 costs 1,999 pounds at launch [1], and 128 GB of memory [1].")


def test_junk_reply_is_asked_again(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch)
    bad = {"summary": 1}

    def chat(self, prompt, **kw):
        if "Write the opening" in prompt and bad["summary"]:
            bad["summary"] -= 1
            return "/" * 2000
        return fake_chat(self, prompt, **kw)
    monkeypatch.setattr(Model, "chat", chat)
    report = run.run()
    assert "## Summary" in report and "/" * 30 not in report
    assert not store.meta["unwritten"]
    assert any("asking again" in e["msg"] for e in store.events())


def test_part_still_junk_is_marked_not_hidden(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch)

    def chat(self, prompt, **kw):
        if "Write the opening" in prompt or "Section heading:" in prompt:
            return "/" * 2000
        return fake_chat(self, prompt, **kw)
    monkeypatch.setattr(Model, "chat", chat)
    report = run.run()
    assert "/" * 30 not in report
    assert "## Summary\n\n" + pl.UNWRITTEN in report
    assert len(store.meta["unwritten"]) >= 2                 # the summary and at least one finding
    assert report.count(pl.UNWRITTEN) == len(store.meta["unwritten"])
    assert "Summary" in store.meta["unwritten"]
    assert any("could not be written" in r for r in store.meta["trust"]["reasons"])


def test_parts_that_fail_twice_get_one_more_go(tmp_path, monkeypatch):
    # both tries inside one call fail, so only the ask after the writing wave can save these parts
    run, store = make_run(tmp_path, monkeypatch)
    bad = {"against": 2, "section": 2}

    def chat(self, prompt, **kw):
        key = "against" if "## The case against" in prompt else "section" if "Section heading:" in prompt else ""
        if key and bad[key]:
            bad[key] -= 1
            return "/" * 2000
        return fake_chat(self, prompt, **kw)
    monkeypatch.setattr(Model, "chat", chat)
    report = run.run()
    assert bad == {"against": 0, "section": 0}
    assert pl.UNWRITTEN not in report and not store.meta["unwritten"]
    assert "One user measured 30 tokens per second" in report


def test_unreadable_model_is_named_not_blamed_on_search(tmp_path, monkeypatch):
    run, store = make_run(tmp_path, monkeypatch)

    def chat(self, prompt, stage="", **kw):
        if stage == "extract":
            return "/" * 2000
        return fake_chat(self, prompt, stage=stage, **kw)
    monkeypatch.setattr(Model, "chat", chat)
    report = run.run()
    assert store.meta["status"] == pl.BUSY
    assert "unreadable text" in report and "did not find pages" not in report
    assert store.meta["stats"]["unreadable"] >= 1


def test_dropped_refs_leave_no_stray_comma():
    assert pl._drop_bad_refs("No drop by house age [8], [9]. Next [1].", 3) == "No drop by house age. Next [1]."
    assert pl._drop_bad_refs("Costs 5, 6 or 7 pounds [2].", 3) == "Costs 5, 6 or 7 pounds [2]."
