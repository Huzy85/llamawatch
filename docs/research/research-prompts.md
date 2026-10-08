# llamawatch Research: stage prompts (draft v2)

These are the instructions each model gets during a research run. Any model can run
any stage (local or paid). Placeholders are in `{braces}`. Every stage writes plain
Markdown so a small local model can follow it and a person can read the notes.

Depth sets the effort and the report length:

| Depth | Sub-questions | Searches per sub-question | Report length |
|---|---|---|---|
| Quick | 2-3 | up to 6 | 2-3 pages |
| Standard | 3-5 | up to 12 | 5-8 pages |
| Deep | 5-7 | up to 20 | 10-20 pages |

Rules that apply to every stage:
- Never invent a fact, number, name, date, quote or source. If you cannot find it, write it down as a gap.
- Every fact carries the link it came from. Facts from your own training data are not sources.
- Prefer primary sources (the maker, the regulator, the study, the dataset) over articles about them.
- Write today's date as `{today}`. Treat anything older than 12 months as possibly out of date and say so.
- Plain English. No hype words.
- Label estimates as estimates. A figure someone calculated or predicted is never a measurement.
- Never do sums with money, energy or time yourself. Give the inputs with their sources; the app does the maths.
- "No benchmark found" is a gap, not proof that something cannot be done.

v2 changes come from the first full test run (docs/research/sample-run/), where the
Stage 4 check found 9 of 17 claims wrong or weak.

---

## Stage 1. Scope

```
You are planning a research job. Do not research yet.

Question: {question}
Depth: {depth}
Today: {today}
Extra context from the user: {context or "none"}

Write a research plan in Markdown with exactly these sections:

## Restated question
One or two sentences. Make hidden assumptions explicit (country, time period,
budget, who is asking).

## Unclear points
Anything that would change the research if read differently. For each, say
which reading you will use and why. Write "none" if nothing is unclear.

## Sub-questions
{n} sub-questions. Together they must cover the whole question and must not
overlap. For each give:
- the sub-question
- what a complete answer contains (the facts or numbers needed)
- the best kinds of source for it

## Known techniques
Methods, tools or settings an expert would expect the research to cover (for
example a software option that changes the answer). Gatherers must check each
one. Write "none" only if you are sure.

## Landscape
Who else works in this area or has already answered a similar question
(products, companies, studies, communities). These get compared in the report.

## Done means
A short checklist the final report must meet to count as answered.
```

## Stage 2. Gather (run once per sub-question; can run in parallel)

```
You are one researcher on a team. Cover ONLY this sub-question.

Main question: {question}
Your sub-question: {sub_question}
A complete answer contains: {complete_answer}
Good sources: {source_types}
Search budget: {max_searches} searches. Read full pages, not just snippets.
Today: {today}

Loop: list what you still do not know, search (short queries), open the best
results in full, take notes. Stop when the sub-question is answered, nothing
new is turning up, or the budget is spent.

Check each source before using it: is it the original source or a copy? Is it
fact or prediction? Is it dated? Does it sell the thing it praises?
Blogs, "best X of 2026" guides and pages built for search traffic are
[secondary] at best, never [primary], and never the only source for a number.
If the post predates the product it talks about, discard it.

Sanity-check every claim against simple physical limits before writing it
down: does it fit (size, memory, power), is the date possible, does the
price match other sources? If not, flag it instead of recording it.

Every performance figure states the conditions it was measured under (setup,
settings, load). A figure without conditions is weak.
Known techniques to cover: {known_techniques}

Write your notes in Markdown with exactly these sections:

## Answer
2-3 sentences.

## Findings
One line per fact: the fact, then the link. Include numbers, dates, prices
and model names exactly as the source gives them. Mark the source type in
brackets: [primary], [secondary], [forum], [vendor].
If two sources disagree, write both on one line: "X says A (link); Y says B (link)".

## Inferences
Conclusions you draw from the findings. Say which findings they rest on.

## Gaps
What you could not find or confirm, and why.

## Sources read
Every page you opened: title, link, date published (or "undated"), and one
line on how reliable it is.
```

## Stage 3. Findings

```
Read all the researcher notes below and turn them into a numbered list of
claims that answer the main question.

Main question: {question}
Notes: {all_stage2_notes}

For each claim:
- C{n}: the claim in one sentence
- Support: the finding lines and links it rests on
- Strength: strong (2+ independent sources, or 1 primary), moderate (1 good
  secondary), weak (forum, vendor, or undated)
- Matters because: one line

Also list:
## Conflicts
Where sources disagree and what each side says.
## Still missing
Gaps that would change the answer if filled.
```

## Stage 4. Verify (use a different model, or a fresh context, from Stage 3)

```
You are a sceptical fact-checker. You did not write these claims. Your job is
to find what is wrong with them.

Claims: {stage3_claims}
Budget: {max_checks} page opens and searches.

For each claim marked strong or moderate, and every claim with a number in it:
1. Re-check simple limits first (does it fit, is the date possible, do the
   numbers add up). Claims that fail are dropped without opening the source.
2. Open the cited source and check it says what the claim says. Watch for
   wrong numbers, wrong units, old prices, a vendor claim stated as fact, or a
   claim stretched past what the source says.
3. Check the source type. A blog labelled [primary] is relabelled.
4. Search once for evidence against the claim.

Write a verification log in Markdown:

| Claim | Result | What was checked | Change |
|---|---|---|---|
| C1 | confirmed / corrected / weakened / dropped | source reopened, counter-search terms | the corrected wording, or "none" |

Then list any new facts you found, with links, in the same format as the
researcher notes.
```

## Stage 5. Gap round (once only, optional)

Run Stage 2 again only for the items under "Still missing" that would change
the answer. Skip it if nothing important is missing. Never run it twice.

## Stage 6. Write the report

```
Write the final research report from the verified claims, the verification
log and the researcher notes. Use only verified or corrected claims. Drop
anything marked dropped. Keep weakened claims only with a clear caveat.

Question: {question}
Depth: {depth} (aim for {page_target} pages; longer is fine only if the
material needs it)

Use exactly these sections, in this order. The page renders them, so keep
the headings as written (the parts in angle brackets are yours to write).

# <A specific title, under 10 words>

## Summary
Answer the question in the first sentence. Then 1-2 paragraphs: the
recommendation or conclusion, the main reasons, and the biggest caveat. A
reader who stops here should know the answer.

## Key numbers
A table of the 4-8 numbers that matter most, each with its source.

## Background
What the reader needs to know to follow the rest. Keep it short.

## <Finding section headers, 3-6 of them>
Each header states the finding itself, with a number if possible
("Unified memory is the cheapest route past 96GB", not "Memory"). Write in
paragraphs, not bullet lists. Cite inline as [n] using the source numbers
from the Sources section. Use tables where they compare things.

## Who else is doing this
The landscape: other products, approaches, studies or people working on the
same problem, and how they compare. A comparison table is usually right here.

## The case against
The strongest evidence and arguments against the main conclusion, stated
fairly.

## Risks and unknowns
What could make this answer wrong, and what was not settled.

## Recommendations
Concrete next steps, in order. Skip this section only if the question asks
for facts, not a decision.

## How this was researched
Sub-questions, number of searches, pages read, sources used, and claims
checked (confirmed / corrected / dropped). Two short paragraphs.

## Verification log
The Stage 4 table.

## Sources
Numbered. Title, publisher, link, date published, date read, and type
(primary / secondary / vendor / forum).
```

## Stage 7. Final check (automatic, no model)

The app checks the Markdown before showing it:
- every `[n]` points to a source that exists
- every source is cited at least once
- every required heading is present
- no claim marked dropped in the log appears in the text

- every calculated figure (cost, energy, conversions) was made by the app from
  sourced inputs, not written by the model

If a check fails, the report goes back to Stage 6 once with the list of problems.
