"""Run the research test set and compare two ways of running it.

    python -m llamawatch.research.evaluate --url http://127.0.0.1:8080 --model-id my-model \
        --searxng http://127.0.0.1:8890 --variant new --only buy-llmbox

Variants:
  baseline  no web scan before planning, section-by-section writing, no reranker
  new       web scan, writing mode chosen by model size, reranker if --reranker is given

Each run is saved like any other research run. summary.md lists the numbers a
change has to improve: trust grade, citation precision, claims lost in
checking, primary sources, parts of the question left unanswered, repeated
sentences across sections, time and tokens. Reading the reports is still the
final test; the numbers show where to look.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

from .llm import Model, ModelSpec, chat_url
from .pipeline import ResearchRun
from .rerank import Reranker
from .store import RunStore
from .web import Reader, Searcher

QUESTIONS = Path(__file__).resolve().parents[2] / "docs" / "research" / "eval-questions.json"


def repeated(report: str) -> int:
    """Sentence pairs in different body sections that say much the same thing."""
    body = report.split("## How this was researched")[0]
    secs = [s for s in re.split(r"^## ", body, flags=re.M)[1:]
            if not s.startswith(("Key numbers", "Summary"))]
    sents = []
    for i, s in enumerate(secs):
        for x in re.split(r"(?<=[.!?])\s+", s):
            w = set(re.findall(r"[a-z0-9]+", x.lower()))
            if len(w) >= 8:
                sents.append((i, w))
    n = 0
    for a in range(len(sents)):
        for b in range(a + 1, len(sents)):
            if sents[a][0] != sents[b][0]:
                wa, wb = sents[a][1], sents[b][1]
                if len(wa & wb) / len(wa | wb) >= 0.6:
                    n += 1
    return n


def metrics(store: RunStore, report: str) -> dict:
    m = store.meta
    claims = [c for c in store.claims.values() if not c.id.startswith("X")]
    lost = sum(1 for c in claims if c.check in ("dropped", "weakened"))
    src = re.findall(r"^\d+\. .*\. (Primary|Secondary|Vendor|Forum)\.$", report, re.M)
    return {
        "status": m.get("status"), "trust": (m.get("trust") or {}).get("level", "-"),
        "precision": m.get("citation_precision"), "claims": len(claims),
        "lost_pct": round(100 * lost / len(claims)) if claims else None,
        "sources": len(src), "primary": sum(1 for k in src if k == "Primary"),
        "unanswered": len(m.get("uncovered") or []), "fact_problems": m.get("check_problems"),
        "style_problems": m.get("style_problems"), "repeats": repeated(report),
        "write_mode": m.get("write_mode", ""), "minutes": round((m.get("seconds") or 0) / 60, 1),
        "tokens": m.get("tokens"), "run": store.run_id,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", required=True, help="OpenAI-compatible base URL of the model")
    ap.add_argument("--model-id", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--context", type=int, default=65536)
    ap.add_argument("--size", default="", help="small, medium or large; read from the name if left out")
    ap.add_argument("--no-thinking", action="store_true")
    ap.add_argument("--searxng", default="")
    ap.add_argument("--webclaw", default="")
    ap.add_argument("--reranker", default="", help="rerank server base URL (used by the new variant)")
    ap.add_argument("--variant", choices=("baseline", "new"), default="new")
    ap.add_argument("--depth", default="quick")
    ap.add_argument("--only", default="", help="comma-separated question ids")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=600, help="seconds per model call (CPU models need more)")
    ap.add_argument("--reverse", action="store_true", help="take questions from the end of the list")
    ap.add_argument("--tag", default="", help="label for this model in the results, e.g. local-main")
    ap.add_argument("--out", default=str(Path.home() / ".local/share/llamawatch/research-eval"))
    a = ap.parse_args(argv)

    qs = json.loads(QUESTIONS.read_text())
    if a.only:
        keep = set(a.only.split(","))
        qs = [q for q in qs if q["id"] in keep]
    tag = a.tag or re.sub(r"[^a-z0-9]+", "-", a.model_id.lower()).strip("-")
    out = Path(a.out) / f"{a.variant}-{tag}"
    out.mkdir(parents=True, exist_ok=True)
    spec = ModelSpec(name=a.name or a.model_id, url=chat_url(a.url), model=a.model_id,
                     context=a.context, no_thinking=a.no_thinking, size=a.size)
    new = a.variant == "new"
    opts = {"scan": new, "one_shot": "auto" if new else False}
    rows_file = out / "results.jsonl"
    if a.reverse:
        qs = qs[::-1]
    claims = out / "claimed"
    claims.mkdir(exist_ok=True)
    for q in qs:
        try:    # several runners can share one model's question list
            (claims / q["id"]).touch(exist_ok=False)
        except FileExistsError:
            continue
        store = RunStore(out / "runs")
        run = ResearchRun(q["question"], a.depth, Model(spec, timeout=a.timeout), store,
                          Searcher({"searxng_url": a.searxng, "webclaw_path": a.webclaw}),
                          Reader({"webclaw_path": a.webclaw}, cache_dir=Path(a.out) / "_cache"),
                          workers=a.workers, options=opts,
                          reranker=Reranker(a.reranker) if (new and a.reranker) else None,
                          on_event=lambda e, i=q["id"]: print(time.strftime("%H:%M:%S"), i, e["stage"],
                                                              e["msg"][:120], flush=True))
        try:
            report = run.run()
        except Exception as e:      # one failed question must not stop the rest
            print(q["id"], "FAILED", repr(e)[:300], flush=True)
            try:
                report = store.read("report.md")
            except Exception:
                report = ""
            store.meta.setdefault("status", f"failed: {e!r}"[:200])
        row = {"id": q["id"], "model": tag, "variant": a.variant, **metrics(store, report)}
        with rows_file.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(json.dumps(row), flush=True)
    write_summary(Path(a.out))


def write_summary(root: Path):
    rows = []
    for f in root.glob("*/results.jsonl"):
        rows += [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    latest = {(r["id"], r.get("model", ""), r["variant"]): r for r in rows}       # a rerun replaces the old row
    cols = ["id", "model", "variant", "trust", "precision", "lost_pct", "sources", "primary", "unanswered",
            "fact_problems", "repeats", "write_mode", "minutes", "tokens"]
    md = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for k in sorted(latest):
        md.append("| " + " | ".join(str(latest[k].get(c, "")) for c in cols) + " |")
    (root / "summary.md").write_text("\n".join(md) + "\n")


if __name__ == "__main__":
    main()
