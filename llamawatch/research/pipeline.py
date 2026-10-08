"""The research run: scope, gather, claims, verify, gap round, write, check.

The app runs the loop. Models are asked small, focused questions: plan this,
read this page, what is missing, turn these quotes into claims, check these
claims, write this section. That keeps every prompt inside a 32-65K local
model's window and lets a paid model run the same steps, only better.

Free checks done in code, whatever the model:
- every quote must exist in the stored page, or it is thrown away
- every number in a claim must appear in that claim's quotes
- every number in a cited sentence must appear in the cited page
- citations must point at real sources; dropped claims must not reappear
- strength comes from counting independent sites, not from the model
- every limit in the question and every "done means" item must be covered by
  a claim, or the report lists it as a gap

Design choices that follow published results (docs/research-design-evidence.md):
- plan from a web search, not from the model's memory (STORM)
- small steps with short inputs, because small models lose track in long ones (RULER)
- a model that can hold the whole report writes it in one go; parallel section
  writers give disjoint reports (LangChain Open Deep Research). Smaller models
  write one section at a time but see the whole outline
- relevance is judged by a reranker when one is set up, not by the chat model
- no majority voting: it lowers small models' accuracy on hard questions
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

from . import prompts as P
from .llm import BudgetSpent, Model, ModelError, bullets, field_value, sections
from .quotes import context as quote_context, normalise, quote_score
from .rerank import Reranker
from .safety import untrusted
from .style import prompt_rules, style_problems, tidy
from .trust import grade, model_note
from .store import Claim, RunStore
from .web import Reader, Searcher, guess_kind, host, key_terms, rank_hits

log = logging.getLogger(__name__)

_LABEL = re.compile(r"^\s*(?:search(?:es)?|query|q)\s*\d*\s*[:.)-]\s*", re.I)
_LEAD = re.compile(r"^(?:an?\s+)?(?:new\s+)?(?:short\s+)?(?:web\s+)?search(?:es)?\b"
                   r"(?:\s+(?:on|for|about|of|focused on|that finds?))?\s*", re.I)
# what small models copy from the prompt instead of writing a search
_ECHO = re.compile(r"\b(to fill it|different from those already done|short web search|"
                   r"web search|new search)\b", re.I)


def clean_queries(raw) -> list[str]:
    """Searches as a search engine wants them. Small models write "Search 1: ..."
    or copy the instruction itself ("a short web search to fill it"), which
    finds dictionary pages for the word "search"."""
    out = []
    for q in raw:
        q = _LABEL.sub("", (q or "").strip().strip('"*').strip())
        q = _LEAD.sub("", q).strip(' "*.:')
        if not q or _ECHO.search(q) or len(q.split()) < 2:
            continue
        q = " ".join(q.split()[:14])
        if q.lower() not in (x.lower() for x in out):
            out.append(q)
    return out


# Two levels. Quick: one pass, pages read in parallel with short timeouts, no
# counter-searches or gap round, at most one fix per section. Full: the lot.
DEPTHS = {
    "quick": dict(tasks=3, rounds=1, reads=3, pages=3, counter=0, words="120 to 200",
                  sections="2 to 3", gaps=0, quotes=4, fast=True, label="Quick answer",
                  time="4 to 6 minutes",
                  about="Searches the web, reads about 12 pages and gives a short answer with "
                        "sources. It reads fewer pages, so it can miss things."),
    "full":  dict(tasks=5, rounds=3, reads=4, pages=12, counter=8, words="300 to 500",
                  sections="4 to 6", gaps=2, quotes=7, fast=False, label="Full report",
                  time="15 to 30 minutes",
                  about="Reads 25 to 40 pages, checks each claim against its source and against "
                        "other sources, and writes a long report you can print. You can leave "
                        "the page and come back."),
}
OLD_DEPTHS = {"standard": "full", "deep": "full"}     # names used before 2026-10
# shown under the options; describes the tool, never suggests it is fit for decisions
DEPTH_NOTE = ("This is AI research. Treat it as a starting point and check the sources before "
              "relying on anything. Results depend on the model: small local models miss more "
              "and make more mistakes.")
QUOTE_PASS = 0.85
# reranker score above which a claim speaks to a "done means" item. Set on
# bge-reranker-v2-m3 (raw logits): on-topic pairs scored about -2, off-topic -7
# to -11. Servers that return 0-1 scores never flag a gap at this level, which
# is the safe way to be wrong. research.reranker_covered overrides it.
COVERED = -5.0
_NUM = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


class Cancelled(Exception):
    pass


@dataclass
class Task:
    id: str
    question: str
    needed: str = ""
    searches: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    pages: int = 0


def numbers(text: str) -> set[str]:
    """Numbers that carry meaning: skip 1-digit list counters and years alone."""
    out = set()
    for m in _NUM.findall(text or ""):
        n = m.replace(",", "")
        if len(n.replace(".", "")) >= 2 or "." in n:
            out.add(n)
    return out


def grounded(nums: set[str], text: str) -> set[str]:
    """Numbers from nums that do not appear in text."""
    hay = {m.replace(",", "") for m in _NUM.findall(text or "")}
    return {n for n in nums if n not in hay}


class ResearchRun:
    def __init__(self, question: str, depth: str, model: Model, store: RunStore,
                 searcher: Searcher, reader: Reader, verifier: Model | None = None,
                 context: str = "", workers: int = 3, on_event=None,
                 today: str | None = None, reranker: Reranker | None = None,
                 options: dict | None = None):
        self.q = question.strip()
        depth = OLD_DEPTHS.get(depth.lower(), depth.lower())
        self.depth = depth if depth in DEPTHS else "full"
        self.d = DEPTHS[self.depth]
        self.model = model
        self.verifier = verifier or model
        self.store = store
        self.searcher = searcher
        self.reader = reader
        self.context = context.strip() or "none"
        self.workers = max(1, int(workers))
        self.on_event = on_event
        self.today = today or date.today().isoformat()
        self.cancel = threading.Event()
        self.tasks: list[Task] = []
        self.plan: dict = {}
        self.shape = "general"
        self.seen: set[str] = set()
        self.rank: dict[str, int] = {}     # url -> order it was picked, so claim order does not depend on which page loaded first
        self._seen_lock = threading.Lock()
        self.stats = {"searches": 0, "pages_read": 0, "pages_blocked": 0, "quotes_kept": 0,
                      "quotes_rejected": 0, "counter_checks": 0, "gap_tasks": 0, "unreadable": 0}
        self.gaps: list[str] = []
        self.reranker = reranker
        # scan: search the web before planning. one_shot: "auto" (by model size), True or False
        self.opts = {"scan": True, "one_shot": "auto", **(options or {})}
        self.scan_text = ""
        self.uncovered: list[str] = []
        self.write_mode = ""

    # ── plumbing ─────────────────────────────────────────────────────
    def emit(self, stage: str, msg: str, **kw):
        ev = self.store.event(stage=stage, msg=msg, **kw)
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                pass

    def _check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def _spent(self, share: float) -> bool:
        cap = self.model.cap
        return cap is not None and self.model.usage.cost >= cap * share

    def _ask(self, model: Model, template: str, stage: str, max_tokens: int = 1500,
             temperature: float = 0.3, **kw) -> str:
        self._check()
        kw.setdefault("today", self.today)
        kw.setdefault("rules", P.RULES.format(today=self.today))
        kw.setdefault("style", prompt_rules())
        prompt = template.format(**kw)
        out = model.chat(prompt, system=P.SYSTEM, max_tokens=max_tokens, temperature=temperature, stage=stage)
        if not garbled(out):
            return out
        # seen on a busy shared server: whole replies of "////". Ask once more, then give up on this call.
        self.emit(stage, "The model returned unreadable text, asking again", level="warn")
        out = model.chat(prompt, system=P.SYSTEM, max_tokens=max_tokens,
                         temperature=min(1.0, temperature + 0.3), stage=stage)
        if not garbled(out):
            return out
        self.emit(stage, "The model returned unreadable text twice; this part is left out", level="warn")
        with self._seen_lock:
            self.stats["unreadable"] += 1
        return ""

    def _par(self, fn, items, workers: int | None = None):
        with ThreadPoolExecutor(max_workers=workers or self.workers) as ex:
            return list(ex.map(fn, items))

    def _wide(self, items) -> int | None:
        """Quick runs every small job at once (at most 5); Full keeps the set worker count."""
        return min(5, max(self.workers, len(items))) if self.d["fast"] else None

    # ── the whole run ────────────────────────────────────────────────
    def run(self) -> str:
        t0 = time.time()
        self.store.set_meta(question=self.q, depth=self.d["label"], model=self.model.spec.name,
                            verifier=self.verifier.spec.name, status="running",
                            model_note=model_note(f"{self.model.spec.name} {self.model.spec.model}",
                                                  self.model.spec.size),
                            started=time.strftime("%Y-%m-%d %H:%M"), cap=self.model.cap)
        status = "done"
        try:
            self.scan()
            self.scope()
            self.gather_all(self.tasks)
            self.build_claims(self.tasks)
            self.verify(list(self.store.claims.values()))
            self.counter_checks()
            self.gap_round()
            if any(c.check != "dropped" for c in self.store.claims.values()):
                report = self.write()
            elif self.stats["unreadable"]:
                # the pages may have been fine; the model could not read them
                status, report = BUSY, self._no_evidence(busy=True)
            else:
                status, report = "no evidence", self._no_evidence()
        except Cancelled:
            status, report = "cancelled", ""
            self.emit("run", "Stopped by the user")
        except BudgetSpent as e:
            status = "stopped: spending cap reached"
            self.emit("run", str(e))
            report = self._safe_write()
        except ModelError as e:
            status = f"failed: {e}"
            self.emit("run", f"Model error: {e}", level="error")
            report = self._safe_write()
        finally:
            self.reader.close()
        u = self.model.usage
        self.store.set_meta(status=status, seconds=round(time.time() - t0),
                            calls=u.calls, tokens=u.tokens_in + u.tokens_out,
                            tokens_in=u.tokens_in, tokens_out=u.tokens_out,
                            cost=round(u.cost, 4), by_stage=u.by_stage, stats=self.stats,
                            search_health=self.searcher.health, finished=time.strftime("%Y-%m-%d %H:%M"))
        self.emit("run", f"Finished: {status}", status=status)
        return report

    def _no_evidence(self, busy: bool = False) -> str:
        """Nothing survived checking. A model asked to write from nothing makes
        things up, so the app says so and lists what it tried."""
        if busy:
            title, why = "The model could not answer", "the model returned unreadable text"
            text = ("The model kept returning unreadable text, so no report was written. This usually "
                    "means it is busy with other work. Try again later, or pick another model.")
        else:
            title, why = "No answer found", "no usable evidence found"
            text = ("The search did not find pages that answer this question, so no report "
                    "was written. Try wording the question differently, or a larger model.")
        self.emit("write", f"No report written: {why}", level="warn")
        self.uncovered = ["no claim from any page survived checking"]
        md = "\n".join([f"# {title}", "", f"*Question: {self.q}*", "", text, "", self._app_sections()])
        self.store.write("report.md", md)
        self.store.set_meta(title=title, uncovered=self.uncovered,
                            trust={"level": "weak", "reasons": [why]})
        return md

    def _safe_write(self) -> str:
        """After a stop, write a report from what was gathered. If the model cannot
        write either, save the parts the app builds by itself."""
        try:
            if self.store.claims:
                return self.write()
        except (BudgetSpent, ModelError, Cancelled):
            pass
        md = self._app_sections()
        self.store.write("report.md", md)
        return md

    # ── 1. scan and scope ────────────────────────────────────────────
    def scan(self):
        """Search before planning, so the plan names what exists today rather
        than what the model remembers from training."""
        if not self.opts.get("scan"):
            return
        self.emit("scope", "Searching the web before planning")
        hits = []
        for qy in (self.q, f"{self._topic()} options compared"):
            hits += self.searcher.search(qy, n=10)
            self.stats["searches"] += 1
        hits = rank_hits(hits, set())[:14]
        self.stats["scan_hits"] = len(hits)
        self.scan_text = "\n".join(f"- {h.title.strip()} ({host(h.url)}): {h.snippet.strip()[:220]}"
                                   for h in hits)
        self.store.write("scan.md", self.scan_text)

    def scope(self):
        self.emit("scope", "Planning the research")
        md = self._ask(self.model, P.SCOPE, "scope", max_tokens=2500, question=self.q,
                       depth=self.d["label"], context=self.context, n=self.d["tasks"],
                       scan=untrusted("search results", self.scan_text) if self.scan_text
                       else "- no search results (search was off or failed)")
        self.store.write("plan.md", md)
        sec = sections(md)
        kind = (sec.get("report type", "").split() or ["general"])[0].strip(".*").lower()
        self.shape = kind if kind in P.SHAPES else "general"
        self.plan = {
            "restated": sec.get("restated question", ""),
            "techniques": bullets(sec.get("known techniques", "")),
            "landscape": bullets(sec.get("landscape", "")),
            "done": bullets(sec.get("done means", "")),
            "perspectives": bullets(sec.get("perspectives", "")),
            "limits": [b for b in bullets(sec.get("limits", ""))
                       if b.split(":")[-1].strip(" .*").lower() not in ("none", "n/a", "")],
        }
        for m in re.finditer(r"^###\s*T?(\d+)\s*[:.)-]\s*(.+?)\s*$(.*?)(?=^###|^## |\Z)",
                             sec.get("sub-questions", ""), re.M | re.S):
            body = m.group(3)
            searches = clean_queries(field_value(body, "Searches").split("|"))
            self.tasks.append(Task(id=f"T{len(self.tasks) + 1}", question=m.group(2).strip(),
                                   needed=field_value(body, "Complete answer"),
                                   searches=searches[:3] or [m.group(2).strip()]))
        if not self.tasks:                       # the model ignored the format
            self.tasks = [Task(id="T1", question=self.q, needed="a direct answer with sources",
                               searches=[self.q])]
        self.tasks = self.tasks[:self.d["tasks"] + 1]
        # landscape gets its own searches if the plan named anyone
        if self.plan["landscape"] and len(self.tasks) < self.d["tasks"] + 1:
            names = ", ".join(x.split(":")[0].split("(")[0].strip(" *") for x in self.plan["landscape"][:4])
            self.tasks.append(Task(id=f"T{len(self.tasks) + 1}",
                                   question=f"How do others working on this compare: {names}?",
                                   needed="what each one offers, measured results, price or cost",
                                   searches=[f"{n.strip()} {self._topic()}" for n in names.split(",")[:3]]))
        self.store.set_meta(shape=self.shape, tasks=[t.question for t in self.tasks],
                            limits=self.plan["limits"])
        self.emit("scope", f"{len(self.tasks)} sub-questions, report type {self.shape}",
                  tasks=[t.question for t in self.tasks])

    def _topic(self) -> str:
        return " ".join(key_terms(self.q)[:4])

    # ── 2. gather ────────────────────────────────────────────────────
    def gather_all(self, tasks: list[Task], rounds: int | None = None):
        def one(t):
            try:
                self.gather(t, rounds or self.d["rounds"])
            except (Cancelled, BudgetSpent):
                raise
            except ModelError as e:
                self.emit("gather", f"{t.id}: model error, moving on ({e})", level="warn")
        # Quick runs every sub-question at once: two waves of searching and
        # reading was most of the gap between 3 minutes and 6 on Gemma.
        self._par(one, tasks, self._wide(tasks))

    def _claim_url(self, url: str) -> bool:
        with self._seen_lock:
            if url in self.seen:
                return False
            self.seen.add(url)
            self.rank[url] = len(self.rank)
            return True

    def gather(self, t: Task, rounds: int):
        queue = list(t.searches)
        for r in range(rounds):
            self._check()
            if self._spent(0.6):
                self.emit("gather", f"{t.id}: stopping early to stay inside the spending cap")
                break
            qs = queue[:3]
            for qy in qs:
                self.emit("gather", f"{t.id} search: {qy}", task=t.id)
                t.done.append(qy)
                self.stats["searches"] += 1
            with ThreadPoolExecutor(max_workers=len(qs) or 1) as ex:
                hits = [h for found in ex.map(self.searcher.search, qs) for h in found]
            ranked = self._order(t.question + " " + t.needed, rank_hits(hits, set(self.seen)))
            want = min(self.d["reads"], self.d["pages"] - t.pages)
            urls = []
            for h in ranked:
                if len(urls) >= want:
                    break
                if self._claim_url(h.url):
                    urls.append(h.url)
            # pages for one sub-question are read side by side; each site still gets one request at a time
            with ThreadPoolExecutor(max_workers=max(1, min(3, len(urls)))) as ex:
                list(ex.map(lambda u: self.read_page(t, u), urls))
            if t.pages >= self.d["pages"] or r == rounds - 1:
                break
            more, done = self.reflect(t)
            if done and self.depth_gate(t):
                self.emit("gather", f"{t.id}: answered")
                break
            if done:
                self.emit("gather", f"{t.id}: not enough strong evidence yet, keep looking")
            queue = [m for m in more if m not in t.done]
            if not queue:
                break

    def _order(self, query: str, hits: list) -> list:
        """Reranker score when there is one (primary sources get a small lift),
        otherwise primary sources first in search-engine order."""
        sc = self.reranker.scores(query, [f"{h.title}\n{h.snippet}" for h in hits]) if self.reranker else None
        if sc is None:
            return sorted(hits, key=lambda h: guess_kind(h.url) != "primary")
        self.stats["reranked"] = self.stats.get("reranked", 0) + 1
        lift = {id(h): s + (1.0 if guess_kind(h.url) == "primary" else 0.0) for h, s in zip(hits, sc)}
        return sorted(hits, key=lambda h: -lift[id(h)])

    def read_page(self, t: Task, url: str):
        page = self.reader.read(url, fast=self.d["fast"])
        if not page.ok:
            self.stats["pages_blocked"] += 1
            t.blocked.append(f"{url} ({page.error or 'blocked'})")
            self.emit("read", f"{t.id} could not read {host(url)}: {page.error}", task=t.id, url=url, ok=False)
            return
        t.pages += 1
        self.stats["pages_read"] += 1
        rec = self.store.add_page(url, page.text, title=page.title, published=page.published,
                                  retrieval=page.retrieval, kind=guess_kind(url))
        self.emit("read", f"{t.id} read {host(url)} via {page.retrieval}", task=t.id, url=url,
                  ok=True, page=rec.id)
        self.extract(t, rec, page.text)

    def fit(self, text: str, terms: list[str], cap: int, query: str = "") -> str:
        """Long pages: keep the opening and the parts that mention the sub-question."""
        if len(text) <= cap:
            return text
        chunks = re.split(r"\n{2,}", text)
        blocks, cur = [], ""
        for c in chunks:
            if len(cur) + len(c) > 2500 and cur:
                blocks.append(cur)
                cur = ""
            cur += c + "\n\n"
        blocks.append(cur)
        sc = self.reranker.scores(query, blocks[1:]) if (self.reranker and query) else None
        if sc is not None:
            scored = [i + 1 for i in sorted(range(len(sc)), key=lambda i: -sc[i])]
        else:
            scored = sorted(range(1, len(blocks)), reverse=True,
                            key=lambda i: sum(blocks[i].lower().count(w) for w in terms))
        keep, size = {0}, len(blocks[0])
        for i in scored:
            if size + len(blocks[i]) > cap:
                continue
            keep.add(i)
            size += len(blocks[i])
        return "\n[...]\n".join(blocks[i].strip() for i in sorted(keep))

    def extract(self, t: Task, rec, text: str):
        terms = key_terms(t.question + " " + t.needed)
        # Quick sends less of each page: reading the prompt is most of the extract time
        cap = 12000 if self.d["fast"] else 40000
        body = self.fit(text, terms, min(self.model.chars_budget, cap), query=f"{t.question} {t.needed}")
        out = self._ask(self.model, P.EXTRACT, "extract", max_tokens=1800, question=self.q,
                        task=t.question, needed=t.needed or "a direct answer", url=rec.url,
                        title=rec.title or "unknown", page=untrusted("page", body),
                        max_quotes=self.d["quotes"])
        if field_value(out, "RELEVANT").lower().startswith("no"):
            return
        kind = field_value(out, "TYPE").split()[:1]
        if kind and kind[0].strip(".,").lower() in ("primary", "secondary", "vendor", "forum"):
            # a site the app knows is a forum stays a forum, whatever the page claims
            if rec.kind != "forum":
                rec.kind = kind[0].strip(".,").lower()
        pub = field_value(out, "PUBLISHED")
        pub = re.split(r"\s*[(\"]", pub)[0].strip(" .,")
        if pub and pub.lower() != "unknown" and not rec.published:
            rec.published = pub[:40]
        self.store.save_pages()
        kept = 0
        for quote, shows in _quote_pairs(out):
            score = quote_score(quote, text)
            if score < QUOTE_PASS:
                self.stats["quotes_rejected"] += 1
                continue
            self.store.add_evidence(rec.id, quote, shows, task=t.id, score=round(score, 2))
            self.stats["quotes_kept"] += 1
            kept += 1
        if kept:
            self.emit("extract", f"{t.id}: {kept} quote{'s' if kept != 1 else ''} from {host(rec.url)}", task=t.id)

    def _evidence_lines(self, ids, with_quote=False, limit=40) -> str:
        lines = []
        for eid in list(ids)[:limit]:
            e = self.store.evidence[eid]
            p = self.store.pages[e.page_id]
            line = f"- {e.id} ({host(p.url)}, {p.kind}, {p.published or 'undated'}): {e.summary}"
            if with_quote:
                line += f'\n  Quote: "{e.quote[:400]}"'
            lines.append(line)
        return "\n".join(lines) or "- none yet"

    def reflect(self, t: Task) -> tuple[list[str], bool]:
        ids = [e.id for e in self.store.evidence.values() if e.task == t.id]
        out = self._ask(self.model, P.REFLECT, "reflect", max_tokens=600, question=self.q,
                        task=t.question, needed=t.needed or "a direct answer",
                        techniques=", ".join(self.plan.get("techniques", [])[:6]) or "none",
                        evidence=self._evidence_lines(ids),
                        searches="\n".join(f"- {s}" for s in t.done) or "- none",
                        blocked="\n".join(f"- {b}" for b in t.blocked[-6:]) or "- none")
        missing = re.search(r"MISSING:(.*?)(?:DONE:|\Z)", out, re.S)
        t.missing = [m for m in bullets(missing.group(1) if missing else "")
                     if not m.lower().startswith("nothing")]
        done = field_value(out, "DONE").lower().startswith("yes")
        srch = re.search(r"SEARCHES:(.*)", out, re.S)
        return clean_queries(bullets(srch.group(1) if srch else ""))[:3], done

    def depth_gate(self, t: Task) -> bool:
        """No stopping on thin evidence (after Odysseus' depth gate)."""
        evs = [e for e in self.store.evidence.values() if e.task == t.id]
        pages = {e.page_id for e in evs}
        primary = any(self.store.pages[p].kind == "primary" for p in pages)
        if len(evs) < 2 or len(pages) < 2:
            return False
        return primary or len(evs) >= 4

    # ── 3. claim ledger ──────────────────────────────────────────────
    def build_claims(self, tasks: list[Task]):
        self.emit("claims", "Turning evidence into claims")

        def one(t):
            evs = [e for e in self.store.evidence.values() if e.task == t.id]
            if not evs:
                return []
            evs.sort(key=lambda e: (self.rank.get(self.store.pages[e.page_id].url, 1 << 30), int(e.id[1:])))
            ids = [e.id for e in evs]
            out = self._ask(self.model, P.CLAIMS, "claims", max_tokens=2500, question=self.q,
                            task=t.question, evidence=self._evidence_lines(ids, True, 60))
            return [(t.id, line) for line in bullets(out)]

        found = [x for group in self._par(one, tasks, self._wide(tasks)) for x in group]
        claims = list(self.store.claims.values())
        for tid, line in found:
            eids = [e for e in dict.fromkeys(re.findall(r"E\d+", line)) if e in self.store.evidence]
            if not eids:
                continue
            text = re.sub(r"\[[^\]]*E\d+[^\]]*\]|\{(estimate|against)\}", "", line).strip(" .;-")
            if len(text) < 12:
                continue
            c = Claim(id="", text=text + ".", evidence=eids, task=tid,
                      estimate="{estimate}" in line, against="{against}" in line)
            twin = next((x for x in claims if _same(x.text, c.text)), None)
            if twin:
                twin.evidence = list(dict.fromkeys(twin.evidence + c.evidence))
                continue
            claims.append(c)
        for i, c in enumerate(claims, 1):
            c.id = c.id or f"C{i}"
            c.strength = self.strength(c)
        self.store.set_claims(claims)
        self.emit("claims", f"{len(claims)} claims")

    def strength(self, c: Claim) -> str:
        pages = [self.store.pages[self.store.evidence[e].page_id] for e in c.evidence
                 if e in self.store.evidence]
        hosts = {host(p.url) for p in pages}
        kinds = {p.kind for p in pages}
        if "primary" in kinds or (len(hosts) >= 2 and kinds - {"forum", "vendor"}):
            s = "strong"
        elif kinds - {"forum", "vendor"} or len(hosts) >= 2:
            s = "moderate"
        else:
            s = "weak"
        return "moderate" if c.estimate and s == "strong" else s

    # ── 4. verify ────────────────────────────────────────────────────
    def verify(self, claims: list[Claim]):
        todo = [c for c in claims if not c.check]
        if not todo:
            return
        self.emit("verify", f"Checking {len(todo)} claims against their sources")
        # free check: numbers in the claim must be in its quotes
        for c in todo:
            quotes = " ".join(self.store.evidence[e].quote for e in c.evidence)
            missing = grounded(numbers(c.text), quotes)
            if missing:
                c.check_note = f"number not in source: {', '.join(sorted(missing))}"
        batches = [todo[i:i + 5] for i in range(0, len(todo), 5)]

        def one(batch):
            if self._spent(0.85):
                return ""
            blocks = []
            for c in batch:
                b = f"{c.id} ({c.strength}): {c.text}"
                if c.check_note:
                    b += f"\nApp note: {c.check_note}."
                for e in c.evidence[:3]:
                    ev = self.store.evidence[e]
                    p = self.store.pages[ev.page_id]
                    ctx = quote_context(ev.quote, self.store.page_text(p.id), 500)
                    b += f"\nQuote {e} ({host(p.url)}, {p.kind}, {p.published or 'undated'}): \"{ctx}\""
                blocks.append(b)
            return self._ask(self.verifier, P.VERIFY, "verify", max_tokens=1600, temperature=0.1,
                             claims=untrusted("claims", "\n\n".join(blocks)))

        outs = self._par(one, batches, self._wide(batches))
        for batch, out in zip(batches, outs):
            verdicts = _verdicts(out)
            for c in batch:
                v = verdicts.get(c.id)
                quotes = " ".join(self.store.evidence[e].quote for e in c.evidence)
                if not v:
                    c.check = "weakened" if c.check_note else "confirmed"
                    c.check_note = c.check_note or "quotes checked by the app only"
                    continue
                verdict, fix, why, counter = v
                c.counter = counter or ""
                if verdict == "full" and not c.check_note:
                    c.check, c.check_note = "confirmed", why
                elif verdict == "none" or fix.lower().startswith("drop"):
                    c.check, c.check_note = "dropped", why
                elif fix and fix.lower() != "none" and not grounded(numbers(fix), quotes):
                    c.check, c.corrected, c.check_note = "corrected", fix.rstrip(".") + ".", why
                else:
                    c.check = "weakened"
                    c.check_note = "; ".join(x for x in (c.check_note, why) if x)
        self.store.save_claims()
        n = {k: sum(1 for c in todo if c.check == k) for k in ("confirmed", "corrected", "weakened", "dropped")}
        self.emit("verify", " / ".join(f"{v} {k}" for k, v in n.items()), **n)

    def counter_checks(self):
        """Search once against the claims the answer leans on most."""
        pool = [c for c in self.store.claims.values() if c.check in ("confirmed", "corrected")
                and not c.against]
        pool.sort(key=lambda c: (c.strength != "strong", not numbers(c.text)))
        pool = pool[:self.d["counter"]]
        if not pool:
            return
        self.emit("verify", f"Looking for evidence against {len(pool)} key claims")

        def one(c):
            if self._spent(0.85):
                return
            qy = c.counter or " ".join(key_terms(c.corrected or c.text)[:6])
            self.stats["searches"] += 1
            self.stats["counter_checks"] += 1
            for h in rank_hits(self.searcher.search(qy), set(self.seen))[:3]:
                if not self._claim_url(h.url):
                    continue
                page = self.reader.read(h.url)
                if not page.ok:
                    continue
                rec = self.store.add_page(h.url, page.text, title=page.title,
                                          published=page.published, retrieval=page.retrieval,
                                          kind=guess_kind(h.url))
                out = self._ask(self.verifier, P.COUNTER, "counter", max_tokens=500,
                                claim=c.corrected or c.text, url=h.url,
                                page=untrusted("page", self.fit(page.text, key_terms(c.text),
                                                                min(self.verifier.chars_budget, 24000),
                                                                query=c.corrected or c.text)))
                verdict = field_value(out, "VERDICT").lower()
                pair = _quote_pairs(out.replace("WHY:", "- shows:"))
                if not pair or quote_score(pair[0][0], page.text) < QUOTE_PASS:
                    return
                ev = self.store.add_evidence(rec.id, pair[0][0], pair[0][1], task="counter")
                if verdict.startswith("contradicts"):
                    c.check = "weakened"
                    c.check_note = f"{host(h.url)} says otherwise ({ev.id})"
                    self.store.add_claim(Claim(
                        id="", text=pair[0][1] or pair[0][0], evidence=[ev.id], task="counter",
                        against=True, check="confirmed",
                        strength=self.strength(Claim(id="", text="", evidence=[ev.id])),
                        check_note=f"found while checking {c.id}"))
                elif verdict.startswith("supports"):
                    c.evidence.append(ev.id)
                    c.check_note = (c.check_note + "; " if c.check_note else "") + f"backed by {host(h.url)}"
                    c.strength = self.strength(c)
                return

        self._par(one, pool)
        self.store.save_claims()

    # ── 5. gap round ─────────────────────────────────────────────────
    def gap_round(self):
        done = self.plan.get("done") or []
        live = [c for c in self.store.claims.values() if c.check != "dropped"]
        if not done or not self.d["gaps"] or self._spent(0.6):
            return
        out = self._ask(self.model, P.GAPS, "gaps", max_tokens=500, question=self.q,
                        done="\n".join(f"- {d}" for d in done),
                        claims="\n".join(f"- {c.corrected or c.text}" for c in live[:60]) or "- none",
                        n=self.d["gaps"])
        items = [b for b in bullets(out.split("MISSING:", 1)[-1]) if not b.lower().startswith("nothing")]
        new = []
        for i, item in enumerate(items[:self.d["gaps"]], 1):
            what, _, qy = item.partition("|")
            searches = clean_queries([qy, what])[:1]
            if searches:
                new.append(Task(id=f"G{i}", question=what.strip(), needed=what.strip(), searches=searches))
        if not new:
            return
        self.stats["gap_tasks"] = len(new)
        self.emit("gaps", f"One more round for {len(new)} gaps", gaps=[t.question for t in new])
        self.tasks += new
        self.gather_all(new, rounds=2)
        self.build_claims(new)
        self.verify(list(self.store.claims.values()))

    # ── 6 and 7. write and check ─────────────────────────────────────
    def write(self) -> str:
        self.emit("write", "Writing the report")
        claims = [c for c in self.store.claims.values() if c.check != "dropped"]
        body = [c for c in claims if not c.against]
        self.src = self._number_sources(claims)
        lines = {c.id: self._claim_line(c) for c in claims}

        limits = "\n".join(f"- {x}" for x in self.plan.get("limits", [])) or "- none"
        self.uncovered = self.coverage(claims)

        # outline. Quick skips it and uses one section per sub-question.
        out = "" if self.d["fast"] else self._ask(
            self.model, P.OUTLINE, "outline", max_tokens=800, question=self.q,
            shape=P.SHAPES[self.shape], sections=self.d["sections"], limits=limits,
            claims="\n".join(f"- {c.id} ({c.strength}): {c.corrected or c.text}" for c in body))
        plan = []
        for m in re.finditer(r"^##\s+(.+?)\s*\n([^#]*)", out, re.M):
            ids = [i for i in re.findall(r"[CX]\d+", m.group(2)) if i in lines]
            if ids:
                plan.append((re.sub(r"^\d+[.)]\s*", "", m.group(1).strip().strip("*")), ids))
        if not plan:
            by_task: dict[str, list[str]] = {}
            for c in body:
                by_task.setdefault(c.task, []).append(c.id)
            names = {t.id: t.question for t in self.tasks}
            plan = [(names.get(k, "Findings").rstrip("?"), v) for k, v in by_task.items()]

        outline = "\n".join(f"- {h}" for h, _ in plan)

        def write_section(item):
            heading, ids = item
            text = self._ask(self.model, P.SECTION, "write", max_tokens=2200, question=self.q,
                             shape=P.SHAPES[self.shape], heading=heading, words=self.d["words"],
                             limits=limits, outline=outline,
                             claims="\n".join(lines[i] for i in ids))
            return heading, ids, _strip_heading(text, heading)

        self.write_mode = "in one go" if self._one_shot(plan, lines) else "section by section"
        self.store.set_meta(write_mode=self.write_mode)
        headings = [h for h, _ in plan if h.lower() != "who else is doing this"]
        against = [c for c in claims if c.against or c.check == "weakened"]
        gaps = self._gap_list()

        # The body, the opening, the case against and the next questions do not
        # depend on each other's text, so they are written side by side.
        def ask_next():
            try:
                return self._ask(self.model, P.NEXT, "write", max_tokens=300, question=self.q,
                                 gaps="\n".join(f"- {g}" for g in gaps[:10]) or "- none recorded")
            except (BudgetSpent, ModelError):
                return None
        def ask_summary():
            return self._ask(self.model, P.SUMMARY, "write", max_tokens=1500, question=self.q,
                             shape=P.SHAPES[self.shape], headings="; ".join(headings), limits=limits,
                             claims="\n".join(lines[c.id] for c in body[:50]))

        def ask_against():
            return self._ask(self.model, P.AGAINST, "write", max_tokens=1200, question=self.q,
                             against="\n".join(lines[c.id] for c in against[:20]) or "- none found",
                             gaps="\n".join(f"- {g}" for g in gaps[:15]) or "- none recorded")
        jobs = [ask_summary, ask_against, ask_next]
        if self.write_mode == "in one go":
            jobs.append(lambda: self.write_body(plan, lines, limits))
        else:
            jobs += [lambda item=item: [write_section(item)] for item in plan]
        out = self._par(lambda f: f(), jobs, self._wide(jobs) or max(3, self.workers))
        opening, ag, nxt = out[:3]
        # a part that came back empty is asked for once more, now the wave is over
        if not sections(opening).get("summary", "").strip():
            opening = ask_summary()
        if not ag.strip():
            ag = ask_against()
        written = [w for part in out[3:] for w in part if w[2].strip()]
        missing = [item for item in plan if item[0] not in {h for h, _, _ in written}]
        if written and missing:
            self.emit("write", f"{len(missing)} sections missing from the one-go draft, writing them one by one")
        written += self._par(write_section, missing)
        order = {h: i for i, (h, _) in enumerate(plan)}
        written.sort(key=lambda w: order.get(w[0], 99))
        title = (re.search(r"^#\s+(.+)$", opening, re.M) or [None, self.q])[1].strip()
        osec = sections(opening)
        summary = osec.get("summary", "")
        key_numbers = osec.get("key numbers", "")

        drafted = summary + "\n" + "\n".join(t for _, _, t in written)
        self.uncovered += [f"the report never addresses the limit: {x}" for x in self.plan.get("limits", [])
                           if not _mentions(x, drafted)]
        asec = sections(ag)
        recs = ""
        # Quick gives a short answer, so it skips the recommendations call
        if self.shape not in ("factcheck", "explainer") and not self.d["fast"]:
            recs = _strip_heading(self._ask(self.model, P.RECOMMEND, "write", max_tokens=700,
                                            question=self.q, shape=P.SHAPES[self.shape],
                                            summary=summary, limits=limits), "Recommendations")

        parts = [("Summary", summary, body), ("Key numbers", key_numbers, body)]
        parts += [(h, t, [self.store.claims[i] for i in ids]) for h, ids, t in written
                  if h.lower() != "who else is doing this"]
        parts += [(h, t, [self.store.claims[i] for i in ids]) for h, ids, t in written
                  if h.lower() == "who else is doing this"]
        parts += [("The case against", asec.get("the case against", ""), against),
                  ("Risks and unknowns", asec.get("risks and unknowns", ""), against)]
        if recs:
            parts.append(("Recommendations", recs, body))

        # 7. check every section, rewrite once if needed
        checked, precision = [], []
        def facts(ps):
            return [p for p in ps if not p.startswith("style:")]

        def fixable(ps):
            """Wrong source numbers are removed in code below; a model rewrite for them alone wastes a call."""
            return [p for p in ps if not p.startswith("source number ")]

        # Quick: one model rewrite for the whole report, on the first section with a fact problem
        rewrites = 1 if self.d["fast"] else len(parts)

        for heading, text, cl in parts:
            text = tidy(text)
            if not text.strip():
                checked.append((heading, "", []))
                continue
            problems, prec = self.check_section(text, cl)
            if self.d["fast"]:
                problems = fixable(problems)
            if problems and heading not in ("Key numbers",) and rewrites > 0 \
                    and (not self.d["fast"] or facts(problems)):
                rewrites -= 1
                self.emit("check", f"Fixing '{heading}': {problems[0]}")
                try:
                    text2 = tidy(_strip_heading(self._ask(
                        self.model, P.REWRITE, "rewrite", max_tokens=2200,
                        problems="\n".join(f"- {p}" for p in problems),
                        claims="\n".join(lines[c.id] for c in cl[:40]), text=text), heading))
                    p2, prec2 = self.check_section(text2, cl)
                    if self.d["fast"]:
                        p2 = fixable(p2)
                    # a style fix never buys a broken fact
                    if len(facts(p2)) <= len(facts(problems)) and len(p2) < len(problems):
                        text, problems, prec = text2, p2, prec2
                except (BudgetSpent, ModelError):
                    pass
            if prec is not None:
                precision.append(prec)
            checked.append((heading, _drop_bad_refs(text, len(self.src)), problems))
        score = round(sum(precision) / len(precision), 3) if precision else None
        leftover = sum(len(facts(p)) for _, _, p in checked)
        style_left = sum(len(p) - len(facts(p)) for _, _, p in checked)
        kinds = [self.store.pages[pid].kind for pid in self.src]
        size = self._size()
        # Summary, the findings and the case against must be there; say so when one is not
        must = {"Summary", "The case against"} | {h for h, _ in plan}
        blank = [h for h, t, _ in checked if h in must and not t.strip()]
        blank += [h for h, _ in plan if h not in {c[0] for c in checked}]
        if blank:
            self.emit("write", f"{len(blank)} part(s) of the report could not be written", level="warn")
        self.store.set_meta(title=tidy(title), citation_precision=score, check_problems=leftover,
                            style_problems=style_left, uncovered=self.uncovered, unwritten=blank,
                            trust=grade(self._counts(), self.stats, kinds, score, leftover, len(gaps), size,
                                        unwritten=len(blank)))

        md = [f"# {tidy(title)}", "", f"*Question: {self.q}*", f"*{self._meta_line()}*", ""]
        for heading, text, _ in checked + [(h, "", []) for h in blank if h not in {c[0] for c in checked}]:
            if text.strip():
                md += [f"## {heading}", "", text.strip(), ""]
            elif heading in blank:
                md += [f"## {heading}", "", UNWRITTEN, ""]
        md.append(self._app_sections())
        report = "\n".join(md).strip() + "\n"
        self.store.write("report.md", report)
        self.store.write("sources.json", json.dumps(self._sources_json(claims), ensure_ascii=False))
        if nxt:
            self.store.set_meta(next_questions=[b.strip('"') for b in bullets(nxt)][:3])
        self.emit("write", f"Report written, citation precision {score}", precision=score)
        return report

    def _size(self) -> str:
        sp = self.model.spec
        return model_note(f"{sp.name} {sp.model}", sp.size, sp.paid)["size"]

    def _one_shot(self, plan, lines) -> bool:
        """Write in one go when the model is big enough and the claims fit in its
        window with room to spare; otherwise one section at a time."""
        want = self.opts.get("one_shot", "auto")
        if want != "auto":
            return bool(want)
        size = sum(len(lines[i]) for _, ids in plan for i in ids)
        return self._size() in ("medium", "large") and size < self.model.chars_budget * 0.4

    def write_body(self, plan, lines, limits) -> list[tuple[str, list[str], str]]:
        """All finding sections in one call, then split back by heading. Sections
        the model skipped or renamed are left for the section-by-section writer."""
        draft = "\n\n".join(f"## {h}\n" + "\n".join(lines[i] for i in ids) for h, ids in plan)
        try:
            out = self._ask(self.model, P.BODY, "write", max_tokens=min(12000, 1200 + 900 * len(plan)),
                            question=self.q, shape=P.SHAPES[self.shape], limits=limits,
                            plan=draft, words=self.d["words"])
        except ModelError as e:
            self.emit("write", f"One-go draft failed, writing section by section ({e})", level="warn")
            return []
        found = {}
        for m in re.finditer(r"^##\s+(.+?)\s*$(.*?)(?=^##\s|\Z)", out, re.M | re.S):
            found[_norm_head(m.group(1))] = m.group(2).strip()
        res = []
        for h, ids in plan:
            text = found.get(_norm_head(h), "")
            if len(text) > 80:
                res.append((h, ids, text))
        return res

    def coverage(self, claims: list[Claim]) -> list[str]:
        """'Done means' items no claim speaks to. Checked by the app, so a model
        that says "all covered" cannot hide a hole.

        Needs the reranker: word matching flags too many false gaps ("speed" vs
        "tokens per second"), and a false gap costs the report trust it earned."""
        texts = [c.corrected or c.text for c in claims]
        items = self.plan.get("done", [])
        if not items or not texts or not self.reranker:
            return []
        out = []
        for item in items:
            sc = self.reranker.scores(item, texts)
            if sc is None:
                return []
            if max(sc) < getattr(self.reranker, "covered", COVERED):
                out.append(f"no evidence found for: {item}")
        if out:
            self.emit("check", f"{len(out)} parts of the question have no evidence", items=out)
        return out

    def _sources_json(self, claims: list[Claim]) -> dict:
        """Per source number: where it came from and the quotes the report used."""
        out = {}
        for pid, num in self.src.items():
            p = self.store.pages[pid]
            quotes = list(dict.fromkeys(
                self.store.evidence[e].quote for c in claims for e in c.evidence
                if self.store.evidence[e].page_id == pid))
            out[str(num)] = {"page": pid, "url": p.url, "title": p.title or host(p.url),
                             "site": host(p.url), "kind": p.kind, "published": p.published or "",
                             "read": p.read_at, "via": p.retrieval, "quotes": quotes[:4]}
        return out

    def _number_sources(self, claims: list[Claim]) -> dict[str, int]:
        src: dict[str, int] = {}
        # C2 before C10: compare the numbers, not the text
        for c in sorted(claims, key=lambda c: (c.against, int(c.id[1:] or 0) if c.id[1:].isdigit() else 0)):
            for e in c.evidence:
                pid = self.store.evidence[e].page_id
                src.setdefault(pid, len(src) + 1)
        return src

    def _claim_line(self, c: Claim) -> str:
        nums = sorted({self.src[self.store.evidence[e].page_id] for e in c.evidence
                       if self.store.evidence[e].page_id in self.src})
        tags = [c.strength or "moderate"]
        if c.estimate:
            tags.append("estimate")
        if c.check == "weakened":
            tags.append("weakened: " + (c.check_note or "not fully supported")[:120])
        q = self.store.evidence[c.evidence[0]].quote[:240] if c.evidence else ""
        return (f"- {c.id} [{', '.join(map(str, nums))}] ({'; '.join(tags)}): "
                f"{c.corrected or c.text} Quote: \"{q}\"")

    def check_section(self, text: str, cl: list[Claim]) -> tuple[list[str], float | None]:
        problems = []
        n_src = len(self.src)
        pid_by_n = {v: k for k, v in self.src.items()}
        cited_total = cited_ok = 0
        for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
            refs = [int(x) for g in re.findall(_CITE, sent) for x in re.findall(r"\d+", g)]
            bad = [r for r in refs if r < 1 or r > n_src]
            if bad:
                problems.append(f"source number {bad[0]} does not exist; use only the numbers given")
            nums = numbers(re.sub(_CITE, "", sent)) - {str(r) for r in refs}
            if not nums or sent.lstrip().startswith("|---"):
                continue
            if not refs:
                if not sent.lstrip().startswith(("#", "|")):
                    problems.append(f"no source number after: \"{sent.strip()[:90]}\"")
                continue
            cited_total += 1
            pages = " ".join(self.store.page_text(pid_by_n[r]) for r in refs if r in pid_by_n)
            claims_txt = " ".join((c.corrected or c.text) for c in cl)
            missing = grounded(nums, pages + " " + claims_txt)
            if missing:
                problems.append(f"{', '.join(sorted(missing))} is not in the cited source: \"{sent.strip()[:90]}\"")
            else:
                cited_ok += 1
        live_text = " ".join(c.corrected or c.text for c in self.store.claims.values() if c.check != "dropped")
        for c in self.store.claims.values():
            if c.check == "dropped":
                for n in grounded(numbers(c.text), live_text):
                    if re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", text):
                        problems.append(f"{n} comes from a claim that failed checking ({c.id}); remove it")
        problems = list(dict.fromkeys(problems))[:12] + style_problems(text)
        return problems, (cited_ok / cited_total if cited_total else None)

    def _gap_list(self) -> list[str]:
        out = list(self.uncovered)
        for t in self.tasks:
            out += [f"{m} ({t.id})" for m in t.missing[:3]]
        blocked = [b for t in self.tasks for b in t.blocked]
        if blocked:
            out.append(f"{len(blocked)} pages could not be read (blocked or empty): "
                       + ", ".join(sorted({host(b.split(' ')[0]) for b in blocked})[:8]))
        if self.searcher.health:
            out.append("Search engines unavailable during the run: " + "; ".join(self.searcher.health[:4]))
        return out

    def _counts(self) -> dict:
        cl = [c for c in self.store.claims.values() if not c.id.startswith("X")]
        return {k: sum(1 for c in cl if c.check == k) for k in ("confirmed", "corrected", "weakened", "dropped")}

    def _meta_line(self) -> str:
        n = self._counts()
        total = sum(n.values())
        return (f"Depth: {self.d['label']}. Run: {self.today}. Model: {self.model.spec.name}. "
                f"Sources: {len(getattr(self, 'src', {}))}. Claims checked: {total} "
                f"({n['confirmed']} confirmed, {n['corrected']} corrected, {n['weakened']} weakened, "
                f"{n['dropped']} dropped).")

    def _app_sections(self) -> str:
        """Written by the app from the run record, so the numbers are exact."""
        n = self._counts()
        s = self.stats
        tasks = "; ".join(t.question.rstrip("?") for t in self.tasks if t.id.startswith("T"))
        how = ((f"Before planning, the app searched the web and showed the planner {s.get('scan_hits', 0)} results, "
                "so the plan reflects what exists now. " if s.get("scan_hits") else "")
               + f"The question was split into {sum(1 for t in self.tasks if t.id.startswith('T'))} "
               f"sub-questions: {tasks}. The app ran {s['searches']} searches and read "
               f"{s['pages_read']} pages; {s['pages_blocked']} more could not be read. "
               f"Models read each page and copied out {s['quotes_kept'] + s['quotes_rejected']} quotes; "
               f"the app found {s['quotes_rejected']} of them were not on the page and threw them away.\n\n"
               f"Each claim was checked against its quotes by "
               f"{'a second model' if self.verifier is not self.model else 'a fresh pass of the model'}"
               f", and {s['counter_checks']} key claims were searched for evidence against them. "
               f"Result: {n['confirmed']} confirmed, {n['corrected']} corrected, "
               f"{n['weakened']} weakened, {n['dropped']} dropped."
               + (f" One extra round looked for {s['gap_tasks']} gap{'s' if s['gap_tasks'] != 1 else ''}." if s["gap_tasks"] else "")
               + (" A reranker model chose which search results and page passages to read." if s.get("reranked") else "")
               + (f" The report body was written {self.write_mode}." if self.write_mode else ""))
        rows = ["| Claim | Result | Checks | Change |", "|---|---|---|---|"]
        for c in self.store.claims.values():
            if c.id.startswith("X") or not c.check:
                continue
            what = "quote"
            if "says otherwise" in c.check_note or "backed by" in c.check_note:
                what += ", counter-search"
            change = c.corrected.rstrip(".") if c.check == "corrected" else (
                "removed" if c.check == "dropped" else (c.check_note if c.check == "weakened" else "none"))
            rows.append(f"| {_cell(c.text, 70)} | {c.check} | {what} | {_cell(change, 140)} |")
        src = getattr(self, "src", {})
        srcs = []
        for pid, num in sorted(src.items(), key=lambda kv: kv[1]):
            p = self.store.pages[pid]
            title = (p.title or host(p.url)).replace("\n", " ").rstrip(".")
            srcs.append(f"{num}. {title}. {host(p.url)}. {p.url}. {p.published or 'Undated'}. "
                        f"Read {p.read_at}. {p.kind.capitalize()}.")
        out = ["## How this was researched", "", how, ""]
        if self.uncovered:
            # written by the app, so a hole is shown even if the model leaves it out of the risks
            out += ["Parts of the question this report does not answer:", "",
                    *[f"- {u[0].upper() + u[1:]}" for u in self.uncovered], ""]
        if len(rows) > 2:
            out += ["## Verification log", "", *rows, ""]
        if srcs:
            out += ["## Sources", "", *srcs, ""]
        return "\n".join(out)


# ── helpers ──────────────────────────────────────────────────────────
def _quote_pairs(out: str) -> list[tuple[str, str]]:
    pairs, cur = [], None
    for line in out.splitlines():
        s = line.strip()
        if s.startswith(">"):
            if cur:
                pairs.append((cur, ""))
            cur = s.lstrip("> ").strip().strip('"“”')
        elif cur and re.match(r"^[-*]?\s*shows\s*:", s, re.I):
            pairs.append((cur, s.split(":", 1)[1].strip()))
            cur = None
    if cur:
        pairs.append((cur, ""))
    return [(q, w) for q, w in pairs if len(q) >= 15]


def _verdicts(out: str) -> dict[str, tuple[str, str, str, str]]:
    res = {}
    for m in re.finditer(r"^\**([CX]\d+)\**\s*:\s*\**(full|partial|none)\**(.*?)(?=^\**[CX]\d+\**\s*:|\Z)",
                         out or "", re.M | re.S | re.I):
        body = m.group(3)
        res[m.group(1)] = (m.group(2).lower(), field_value(body, "Fix"),
                           field_value(body, "Why"), field_value(body, "Counter"))
    return res


def _same(a: str, b: str) -> bool:
    ta, tb = set(normalise(a).split()), set(normalise(b).split())
    if not ta or not tb or numbers(a) != numbers(b):
        return False
    return len(ta & tb) / len(ta | tb) >= 0.75


_CITE = r"\[(\d+(?:\s*,\s*\d+)*)\]"


def _norm_head(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"^\d+[.)]\s*", "", h.strip().strip("*#")).lower()).strip()


def _mentions(limit: str, text: str) -> bool:
    """A limit with a number (a budget, a size, a year) is addressed when the
    report uses that number. Limits without numbers are left to the model:
    word matching is too loose to judge them."""
    nums = numbers(re.sub(r"\b\d{4}-\d{2}-\d{2}\b|\b(19|20)\d{2}\b", " ", limit))   # dates and years say when, not how much
    return not nums or not grounded(nums, text)


def _drop_bad_refs(text: str, n_src: int) -> str:
    """Last line of defence: a source number that does not exist never reaches the reader."""
    def fix(m):
        if any(int(x) >= 1000 for x in re.findall(r"\d+", m.group(2))):
            return m.group(0)              # a year, not a source
        ok = [x for x in re.findall(r"\d+", m.group(2)) if 1 <= int(x) <= n_src]
        return m.group(1) + "".join(f"[{x}]" for x in ok) if ok else ""
    out = re.sub(r"(\s*)" + _CITE, fix, text)
    return re.sub(r"[ \t]*,[ \t]*(?=[.;:!?])", "", out)   # "age [8], [9]." with both dropped left "age,."


BUSY = "failed: the model kept returning unreadable text, probably because it is busy. Try again later or pick another model."
UNWRITTEN = ("*This part could not be written: the model returned unreadable text twice. "
             "Run the question again, ideally when the model is less busy.*")


def garbled(text: str) -> bool:
    """Unreadable model output: a long run of one character, or hardly any letters or digits."""
    t = (text or "").strip()
    if not t:
        return False
    if re.search(r"([^\w\s=|-])\1{29,}|(\w)\2{29,}", t):      # "-", "=", "|" make markdown rules and tables
        return True
    words = re.sub(r"(?m)^[\s|:=-]+$", "", t)               # table rules and dividers are not prose
    chars = [c for c in words if not c.isspace()]
    return len(chars) > 80 and sum(c.isalnum() for c in chars) / len(chars) < 0.4


def _strip_heading(text: str, heading: str) -> str:
    lines = text.strip().splitlines()
    def same(x):
        return x.strip().strip("#*: ").lower() == heading.strip().lower()
    while lines and (lines[0].lstrip().startswith("#") or not lines[0].strip() or same(lines[0])):
        lines.pop(0)
    return "\n".join(lines).strip()


def _cell(s: str, n: int) -> str:
    s = (s or "").replace("|", "/").replace("\n", " ").strip()
    if len(s) <= n:
        return s
    cut = s[:n - 1]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:") + "..."
