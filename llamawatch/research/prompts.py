"""What each model is asked at each stage.

Plain Markdown with fixed labels, so a small local model can follow it and the
app can read the answer without strict JSON. The app does the searching,
reading, numbering, counting and checking; models never see a tool.

Rules shared by every prompt are in RULES. Placeholders use str.format.
"""

RULES = """Rules:
- Never invent a fact, number, name, date, quote or source.
- Facts from your own memory are not evidence. Only the text given to you counts.
- Prefer primary sources (the maker, the regulator, the study, the original test).
- Today is {today}. Anything older than 12 months may be out of date.
- Label estimates, forecasts and vendor claims as such. A calculated figure is not a measurement.
- Never add up, multiply or convert money, energy or time yourself.
- Plain English. No hype words."""

SYSTEM = "You are a careful research assistant. You follow the requested format exactly."

SHAPES = {
    "buying": "a buying decision: options compared side by side on price, fit and measured performance, ending with what to pick and when to pick something else",
    "comparison": "a comparison: the criteria that matter, a table comparing the options, and which wins for which kind of user",
    "howto": "a how-to: what is needed, the steps in order, the common mistakes, and how to check it worked",
    "factcheck": "a fact-check: the verdict first (true, false, partly true, unproven), then the evidence each way",
    "explainer": "an explainer: what it is, how it works, why it matters, and what is disputed",
    "general": "a general research report: the answer, the evidence, and what is still unsettled",
}

SCOPE = """You are planning a research job. Do not answer the question yet.

Question: {question}
Depth: {depth}
Today: {today}
Extra context from the user: {context}

What a web search shows today (titles and snippets only; they may be wrong, but they show which products, people, studies and options exist right now, which your memory may not know):
{scan}

Write the plan in Markdown with exactly these sections.

## Restated question
One or two sentences. Make hidden assumptions explicit (country, time period, budget, who is asking).

## Report type
One word from: buying, comparison, howto, factcheck, explainer, general.

## Limits
Hard limits stated in the question or the context: budget, place, date, size, must-haves, must-nots. One bullet each, using the question's own words and numbers. Only what the question or context states: not today's date, not general needs. Write "none" if the question sets no limits.

## Unclear points
Anything that would change the research if read differently, and which reading you will use. Write "none" if nothing is unclear.

## Perspectives
3 to 5 kinds of people who care about this question, one line each with what they would want to know.

## Sub-questions
Exactly {n}. Together they cover the whole question without overlapping. Cover the perspectives above. Write each one like this:
### T1: <the sub-question>
Complete answer: <the facts or numbers a full answer needs>
Searches: <short web search> | <another> | <another>

## Known techniques
Bullet list of methods, tools or settings an expert would expect the research to cover. Write "none" only if you are sure.

## Landscape
Bullet list of the options, products, companies, studies or communities to compare in the report. Include the ones the search results above name, not only the ones you remember.

## Done means
Bullet list: what the final report must contain to count as answered.

{rules}"""

EXTRACT = """You are reading one web page for a research team.

Main question: {question}
Your sub-question: {task}
A complete answer needs: {needed}
Today: {today}
Page address: {url}
Page title: {title}

{page}

Pull out only what helps answer the sub-question. Copy each quote word for word from the page. The app checks every quote against the page and throws away any quote that is not there exactly, so do not tidy, shorten or join sentences.

Reply in exactly this format:
RELEVANT: yes or no
PUBLISHED: the date the page gives, or unknown
TYPE: primary (the maker, regulator, study, dataset or original test), secondary (an article about others' work), vendor (sells the thing it describes), or forum (posts by users)
QUOTES:
> one sentence copied exactly from the page
- shows: what the quote tells us, in your own words, with the conditions behind any measurement (setup, settings, load)
> next sentence copied exactly
- shows: ...

Give up to {max_quotes} quotes. Numbers, prices, dates and measurements matter most. If two parts of the page disagree, quote both. If RELEVANT is no, stop after TYPE.

{rules}"""

REFLECT = """You are one researcher on a team, covering one sub-question.

Main question: {question}
Your sub-question: {task}
A complete answer needs: {needed}
Known techniques the team expects covered: {techniques}
Today: {today}

Evidence so far:
{evidence}

Searches already done:
{searches}

Pages that could not be read:
{blocked}

Decide what is still missing for a complete answer. Reply in exactly this format:
MISSING:
- what is still not known (write "nothing" if the answer is complete)
DONE: yes or no
SEARCHES:
- a new short web search, different from those already done
(up to 3 searches; aim at primary sources, measurements and anything that could prove the evidence wrong)"""

CLAIMS = """Turn this evidence into claims that answer the sub-question.

Main question: {question}
Sub-question: {task}
Today: {today}

Evidence (E number, site, source type, date, quote, what it shows):
{evidence}

Rules for claims:
- One sentence each. Keep numbers exactly as quoted, with their units and conditions.
- Cite the E numbers that support the claim. Use only what the quotes say.
- Never combine numbers into a new number. No sums, no averages, no conversions.
- Add {{estimate}} after a forecast, a calculation, a "up to" vendor figure, or a predicted price.
- Add {{against}} after a claim that cuts against the likely answer or against another claim.
- If sources disagree, write one claim per side, each with its own E numbers.

Reply with a bullet list only, like this:
- <claim> [E2, E5]
- <claim> [E4] {{estimate}}"""

VERIFY = """You are a sceptical fact-checker. You did not write these claims. Find what is wrong with them.

Today: {today}

{claims}

For each claim, compare it with its quotes and decide:
full = the quotes say this, with the same numbers and conditions
partial = the quotes support part of it; the claim stretches, drops a condition, or generalises
none = the quotes do not say this, or the claim breaks a simple limit (it cannot fit, the date is impossible, the numbers clash)

Reply with one block per claim, in this format:
C4: full
Fix: none
Why: one short line
Counter: a short web search that could find evidence against this claim

For partial, give under Fix a corrected sentence the quotes do support. Fix is a replacement claim about the subject, never a comment on the claim ("this claim discusses..."). For none, Fix is "drop"."""

COUNTER = """A fact-checker is looking for evidence against a claim.

Claim: {claim}
Today: {today}
Page address: {url}

{page}

Does this page contradict the claim, support it, or say nothing about it? Copy the deciding sentence word for word.

Reply in exactly this format:
VERDICT: contradicts, supports or unrelated
QUOTE:
> the sentence copied exactly from the page
WHY: one line"""

GAPS = """Check a research job against its goal before the report is written.

Question: {question}
Done means:
{done}

Claims found so far:
{claims}

Which items under "Done means" are still not covered by the claims, and would change the answer if filled? List at most {n}. Reply in exactly this format:
MISSING:
- the missing item | a short web search to fill it
(write "- nothing" if everything that matters is covered)"""

OUTLINE = """Plan the body of a research report.

Question: {question}
Report shape: {shape}
Limits from the question (anything that breaks one is ruled out; say which limit it breaks):
{limits}

Claims (id, strength, text):
{claims}

Group the claims into {sections} finding sections. Each heading states the finding itself, with a number if possible. The heading must be true of the claims under it; never reuse wording from these instructions. Leave out minor claims rather than forcing them in. Then pick the claims that describe others working on the same problem for a section called "Who else is doing this".

Reply in exactly this format, nothing else:
## <finding heading>
C1, C4, C7
## <finding heading>
C2, C9
## Who else is doing this
C5, C11"""

SECTION = """Write one section of a research report.

Question: {question}
Report shape: {shape}
Section heading: {heading}
Today: {today}
Limits from the question (anything that breaks one is ruled out; say which limit it breaks):
{limits}

The whole report, so you know what the other sections cover. Write only your section and do not repeat the others:
{outline}

Claims for this section. The number in square brackets is the source number to cite:
{claims}

Rules:
- Write {words} words in paragraphs, not bullet lists. A Markdown table is welcome where it compares three or more things.
- Every sentence with a fact ends with its source number, like [3]. Use only the source numbers given above.
- Use only these claims. No new facts, numbers, names or dates, not even ones you are sure of.
- Keep every caveat. A claim marked weak or estimate must read as such ("one forum test found", "the maker estimates").
- Never add up, multiply or convert numbers.
- Do not repeat the heading. Do not add a conclusion paragraph or a sentence about the section itself ("This section shows...").
- Never mention claims, quotes, checks or the research process. Write about the subject only.

{rules}

{style}"""

BODY = """Write the body of a research report in one go.

Question: {question}
Report shape: {shape}
Today: {today}
Limits from the question (anything that breaks one is ruled out; say which limit it breaks):
{limits}

The outline. Write every section below, in this order, under its own "## " heading copied exactly. The number in square brackets is the source number to cite:
{plan}

Rules:
- Each section is {words} words in paragraphs, not bullet lists. A Markdown table is welcome where it compares three or more things.
- The sections form one report: each one builds on the last, and nothing is said twice.
- Every sentence with a fact ends with its source number, like [3]. Use only the source numbers given above.
- Use only the claims given for that section. No new facts, numbers, names or dates, not even ones you are sure of.
- Keep every caveat. A claim marked weak or estimate must read as such ("one forum test found", "the maker estimates").
- Never add up, multiply or convert numbers.
- No introduction or conclusion outside the sections. No sentence about the report itself.
- Never mention claims, quotes, checks or the research process. Write about the subject only.

{rules}

{style}"""

SUMMARY = """Write the opening of a research report.

Question: {question}
Report shape: {shape}
Today: {today}

The report's findings (claims with source numbers):
{claims}

The report's main sections: {headings}
Limits from the question (anything that breaks one is ruled out; say which limit it breaks):
{limits}

Reply with exactly these three parts.

# <a title under 10 words that states the answer>

## Summary
Answer the question in the first sentence. Then one or two paragraphs: the conclusion, the main reasons, and the biggest caveat. Cite source numbers like [3] after facts. A reader who stops here should know the answer.

## Key numbers
| What | Number | Source |
|---|---|---|
4 to 8 rows. Copy each number exactly as it appears in a claim above, with its condition in the What column. Source is the number in square brackets, like [3].

{rules}

{style}"""

AGAINST = """Write two short sections of a research report.

Question: {question}
Today: {today}

Evidence against the main answer, or weakened claims:
{against}

Gaps: what the research could not find or confirm:
{gaps}

Reply with exactly these two parts.

## The case against
The strongest evidence and arguments against the main answer, stated fairly, citing source numbers like [3]. Use only the evidence above. If there is none, say plainly that no strong evidence against was found.

## Risks and unknowns
A bullet list of what could make the answer wrong and what was not settled. Use the gaps above.

{rules}

{style}"""

RECOMMEND = """Write the recommendations for a research report.

Question: {question}
Report shape: {shape}

The report's summary:
{summary}

Limits from the question (anything that breaks one is ruled out; say which limit it breaks):
{limits}

Write a numbered list of 3 to 6 concrete next steps, in order, under the heading "## Recommendations". Each step uses only what the summary says and ends with its source number if it rests on a fact. Nothing else.

{style}"""

REWRITE = """This section of a research report failed an automatic check. Rewrite it to fix every problem listed. Keep everything else.

Problems:
{problems}

Claims you may use (source number in square brackets):
{claims}

Section text:
{text}

Reply with the corrected section text only, without the heading.

{style}"""

DRAFTS = """Write two social media drafts from this research report. Use only facts in the report. No hashtags, no emoji, no first person ("I", "we"), no hype words. Open with the answer, not a hook. No "Here's why", no thread bait, no question tacked on at the end to get replies, no stack of one-line paragraphs.

{style}

Report:
{report}

Reply in exactly this format:
SHORT:
<one post under 280 characters that states the answer and the one number that matters most>
LONG:
<150 to 250 words for LinkedIn or Facebook: the question, the answer, two or three reasons with numbers, the biggest caveat>"""

NEXT = """A research report has just been written.

Question: {question}

What it could not settle:
{gaps}

Suggest 3 follow-up research questions a reader would most want answered next. Each must be a complete question someone could research on its own, under 25 words. Reply with a bullet list only."""
