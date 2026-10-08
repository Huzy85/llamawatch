"""On-disk record of one research run.

Everything the run saw is kept: each page as read, each quote taken from it,
each claim and its check. The fact-checker and the report point back to these
files, so any sentence in a report can be traced to the page text behind it.

Layout: <root>/<run_id>/
    run.json        question, depth, model, status, stats, cost
    events.jsonl    progress log (what the UI shows while it runs)
    pages/P<n>.md   page text as read, with a small header
    pages.json      page index: url, title, date, how it was read, type
    evidence.json   quotes: id, page id, quote, summary, sub-question
    claims.json     claims with evidence ids, strength, check result
    report.md       the finished report
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class PageRec:
    id: str
    url: str
    title: str = ""
    published: str = ""          # as the page states it, or ""
    read_at: str = ""
    retrieval: str = ""          # cache | fetch | webclaw | browser | pdf
    kind: str = "secondary"      # primary | secondary | vendor | forum
    chars: int = 0


@dataclass
class Evidence:
    id: str
    page_id: str
    quote: str
    summary: str = ""
    task: str = ""               # sub-question id that found it
    score: float = 1.0           # quote check result


@dataclass
class Claim:
    id: str
    text: str
    evidence: list[str] = field(default_factory=list)
    task: str = ""
    strength: str = ""           # strong | moderate | weak (set by the app)
    against: bool = False        # evidence against the main answer
    estimate: bool = False       # a calculation or forecast, not a measurement
    check: str = ""              # confirmed | corrected | weakened | dropped
    check_note: str = ""
    corrected: str = ""
    counter: str = ""            # search the checker suggested against this claim


class RunStore:
    def __init__(self, root: Path, run_id: str | None = None):
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.dir = Path(root) / self.run_id
        (self.dir / "pages").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.pages: dict[str, PageRec] = {}
        self.by_url: dict[str, str] = {}
        self.evidence: dict[str, Evidence] = {}
        self.claims: dict[str, Claim] = {}
        self.meta: dict = {}
        if run_id and (self.dir / "run.json").exists():
            self._load()

    # ── pages ────────────────────────────────────────────────────────
    def add_page(self, url: str, text: str, **info) -> PageRec:
        with self._lock:
            if url in self.by_url:
                return self.pages[self.by_url[url]]
            pid = f"P{len(self.pages) + 1}"
            rec = PageRec(id=pid, url=url, chars=len(text),
                          read_at=time.strftime("%Y-%m-%d"), **info)
            self.pages[pid] = rec
            self.by_url[url] = pid
            (self.dir / "pages" / f"{pid}.md").write_text(text, encoding="utf-8")
            self._save("pages.json", [asdict(p) for p in self.pages.values()])
            return rec

    def save_pages(self) -> None:
        with self._lock:
            self._save("pages.json", [asdict(p) for p in self.pages.values()])

    def add_claim(self, claim: Claim, prefix: str = "X") -> Claim:
        with self._lock:
            claim.id = f"{prefix}{sum(1 for k in self.claims if k.startswith(prefix)) + 1}"
            self.claims[claim.id] = claim
            return claim

    def page_text(self, pid: str) -> str:
        try:
            return (self.dir / "pages" / f"{pid}.md").read_text(encoding="utf-8")
        except OSError:
            return ""

    # ── evidence and claims ──────────────────────────────────────────
    def add_evidence(self, page_id: str, quote: str, summary: str = "",
                     task: str = "", score: float = 1.0) -> Evidence:
        with self._lock:
            ev = Evidence(id=f"E{len(self.evidence) + 1}", page_id=page_id,
                          quote=quote, summary=summary, task=task, score=score)
            self.evidence[ev.id] = ev
            self._save("evidence.json", [asdict(e) for e in self.evidence.values()])
            return ev

    def set_claims(self, claims: list[Claim]) -> None:
        with self._lock:
            self.claims = {c.id: c for c in claims}
            self._save("claims.json", [asdict(c) for c in claims])

    def save_claims(self) -> None:
        with self._lock:
            self._save("claims.json", [asdict(c) for c in self.claims.values()])

    # ── run info, events, files ──────────────────────────────────────
    def set_meta(self, **kw) -> None:
        with self._lock:
            self.meta.update(kw)
            self._save("run.json", self.meta)

    def event(self, **kw) -> dict:
        kw.setdefault("t", round(time.time(), 2))
        with self._lock, open(self.dir / "events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(kw) + "\n")
        return kw

    def events(self, since: int = 0) -> list[dict]:
        try:
            lines = (self.dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [json.loads(x) for x in lines[since:] if x.strip()]

    def write(self, name: str, text: str) -> None:
        (self.dir / name).write_text(text, encoding="utf-8")

    def read(self, name: str) -> str:
        try:
            return (self.dir / name).read_text(encoding="utf-8")
        except OSError:
            return ""

    def _save(self, name: str, obj) -> None:
        tmp = self.dir / (name + ".tmp")
        tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / name)

    def _load(self) -> None:
        def j(name, default):
            try:
                return json.loads((self.dir / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return default
        self.meta = j("run.json", {})
        for p in j("pages.json", []):
            rec = PageRec(**p)
            self.pages[rec.id] = rec
            self.by_url[rec.url] = rec.id
        for e in j("evidence.json", []):
            self.evidence[e["id"]] = Evidence(**e)
        for c in j("claims.json", []):
            self.claims[c["id"]] = Claim(**c)


def list_runs(root: Path) -> list[dict]:
    out = []
    for d in sorted(Path(root).glob("*/run.json"), reverse=True):
        try:
            out.append(json.loads(d.read_text(encoding="utf-8")) | {"id": d.parent.name})
        except (OSError, ValueError):
            continue
    return out
