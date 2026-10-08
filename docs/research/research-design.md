# llamawatch Research: design (draft v3, 2026-10-07)

Goal: the best report any connected model can produce, local or paid. The app owns
search, page reading, storage and checks. The model only plans, reads, judges and
writes. A small local model and a frontier API model run the same pipeline; the
frontier model just does each step better.

Stage prompts: research-prompts.md. Test run: sample-run/.

## Borrowed ideas (Odysseus is AGPL-3.0, as is llamawatch, so code may be reused with credit; so far only ideas are used)

| Idea | From |
|---|---|
| Per-page extraction against the goal, keeping verbatim quotes | Odysseus (after Alibaba Tongyi DeepResearch) |
| Fetch, then browser fallback when text is empty or weak | Odysseus |
| Depth gate: no stopping before a primary source and enough useful findings | Odysseus |
| Report shape picked by question type (buying, comparison, how-to, fact-check) | Odysseus |
| Coarse source scoring by domain and wording; demote listicles and affiliate pages | Odysseus |
| Page text wrapped as untrusted data; no fetches to private addresses | Odysseus |
| Fast path for simple fact questions on small models | Odysseus |
| Perspectives first: list who cares about the question, ask from each viewpoint | Stanford STORM |
| Outline with citations attached before writing | STORM |
| Lead plans, parallel researchers with their own context, effort scaled to the question | Anthropic multi-agent research write-up |
| Separate citation pass after writing | Anthropic write-up |
| Search, read, reflect loop with a budget, then a forced final answer when spent | Jina node-DeepResearch |
| Compress each researcher's findings before the writer sees them | LangChain open_deep_research |
| Score citations as full / partial / no support | DREAM, DR3-Eval papers |

## Levels

Two, shown to the user with a line of text each and a shared warning (DEPTHS and DEPTH_NOTE in pipeline.py):

- Quick answer, 4 to 6 minutes on a local model (Gemma 4.0 to 5.3 min on 10-07): one search round per sub-question, pages read in parallel with 8 second timeouts and no browser, no counter-searches, no gap round, one model rewrite for the whole report. Wrong source numbers are removed in code.
- Full report, 15 to 30 minutes: every stage below.

Users found anything over a few minutes frustrating (test runs on the old "quick" took 11 to 43 minutes), hence the split.

## Pipeline

0. **Web scan.** Two searches before planning; titles and snippets go to the planner,
   so options and players come from today's web, not the model's memory (STORM).
1. **Scope.** Restate, list the question's hard limits (budget, place, size),  pick the report shape, list perspectives (STORM), sub-questions,
   known techniques, landscape, "done means" checklist. Depth sets the effort:
   Quick 2-3 researchers, Standard 3-5, Deep 5-7.
2. **Gather** (parallel, one researcher per sub-question). Loop: search, read,
   reflect, until answered or budget spent. Every page read goes through extraction:
   the model returns quotes plus a summary, the app stores the page snapshot.
   Depth gate before a researcher may stop.
3. **Claim ledger.** Merge and de-duplicate claims. Each claim links to the exact
   quote(s) and snapshot(s). Strength counts independent sources, not pages.
4. **Verify.** Three layers:
   - App, no model: does each quoted text exist in its stored snapshot? Fails are
     dropped. This catches invented quotes for free, on any model.
   - App, no model: sanity sums (sizes, dates, money) done in code.
   - Model (different model or fresh context): full / partial / no support per
     claim, one counter-search each, source-type relabelling.
5. **Gap round.** Once, only for gaps that would change the answer.
6. **Outline, then write.** A medium or large model (or a paid one) whose claims fit
   in 40% of its window writes the whole body in one go; parallel section writers
   gave LangChain "disjoint" reports. Smaller models write one section at a time
   but each sees the full outline and the limits. Sections the one-go draft skips
   are written singly. "Done means" items no claim speaks to (reranker check) and
   numeric limits the report never uses are listed by the app as unanswered.
7. **Citation pass and final check.** Every sentence with a fact has a citation;
   citation precision is scored; dropped claims absent; all sources cited;
   headings present. One rewrite if it fails.

## Web access (app-side, every model gets it)

Search: SearXNG default (no key). Optional keys: Brave, Tavily, Serper. Results are
de-duplicated, already-read pages skipped, SEO and affiliate pages demoted.

Reading a page, in order:
1. Cache (same URL in this run or recent runs).
2. Plain fetch + main-text extraction. PDFs extracted too.
3. webclaw (MIT, optional binary): browser TLS fingerprint, gets past most 403s.
4. Headless browser (Playwright, optional) for JavaScript-only pages. Closed after each run.
5. Optional hosted reader (Jina Reader or Firecrawl), off by default because it sends
   the URL to a third party.
6. Give up: recorded as "blocked, not read" and listed under gaps.

Polite limits: one request at a time per site, short delay, no logins or paywalls.

## Small-model rules
- Plain Markdown everywhere; JSON only where the app can repair it.
- Page text cut to fit; extraction runs per page, never on the whole pile.
- Writer sees one section's claims at a time, plus the outline of the rest.
- Optional reranker (any /v1/rerank server, `research.reranker_url`) orders search
  results and picks page passages, so relevance does not depend on the chat model.
- No majority voting: it lowered 7-8B models' accuracy on most hard questions
  (arXiv 2608.11403).
- Fast path for simple fact questions.

## How we know it is the best possible
Ten test questions in `docs/research/eval-questions.json` (buying, comparison,
how-to, fact-check, explainer, science, fast-moving, regulation, history).
`python -m llamawatch.research.evaluate` runs them as `baseline` (no scan,
section-by-section, no reranker) or `new`, and writes summary.md: trust grade,
citation precision, claims lost, primary sources, unanswered parts, repeated
sentences across sections, time, tokens. Evidence behind each design choice:
`docs/research-design-evidence.md`.

## Not decided
- Whether hosted readers are offered at all in the public repo.
- Which judge model scores the test set by default.
