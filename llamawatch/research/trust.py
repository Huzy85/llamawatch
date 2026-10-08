"""How far to trust a run: a note about the model before the run, and a grade
from the run's own record after it.

The model note is a rough guide from the model's size. The run grade uses only
what the run measured (checks, sources, gaps), so it works for any topic.
"""

import re

MODEL_NOTES = {
    "small": "Small model. It reads pages and copies quotes reliably, but often misses options, "
             "makes more judgement mistakes and writes thinner reports. Check the key claims yourself.",
    "medium": "Mid-sized model. Good at gathering and checking facts. It can still miss options a "
              "specialist would expect and weigh evidence less well than the largest models.",
    "large": "Large model. The best results this app can give, but the report is still only as good "
             "as the sources it could read.",
    "unknown": "Unknown model size. Quality depends heavily on the model, so treat the report as a "
               "starting point and check the key claims.",
}

GENERAL = ("Research quality depends on the model you pick. Smaller models miss more and judge evidence "
           "less well. Every report shows how its claims were checked, so you can see where it is weak.")


def size_class(name: str, override: str = "", paid: bool = False) -> str:
    """small / medium / large / unknown. A config value wins; otherwise read the size from the name.
    A paid model with no size in its name counts as large; set size_class to say otherwise.

    Mixture-of-experts names like "35B-A3B" are rated by sqrt(total x active), a rough rule of
    thumb for how such models compare with dense ones."""
    if override in MODEL_NOTES:
        return override
    m = re.search(r"(\d+(?:\.\d+)?)\s*[Bb](?:\s*-\s*A(\d+(?:\.\d+)?)\s*[Bb])?(?![a-z])", name or "")
    if not m:
        # paid APIs rarely publish sizes; the ones people pay for are frontier-sized
        return "large" if paid else "unknown"
    total = float(m.group(1))
    size = (total * float(m.group(2))) ** 0.5 if m.group(2) else total
    if size < 15:
        return "small"
    return "medium" if size < 70 else "large"


def model_note(name: str, override: str = "", paid: bool = False) -> dict:
    c = size_class(name, override, paid)
    return {"size": c, "note": MODEL_NOTES[c]}


def grade(counts: dict, stats: dict, kinds: list[str], precision: float | None,
          fact_problems: int, gaps: int, size: str, unwritten: int = 0) -> dict:
    """good / fair / weak, with the reasons a reader can check in the report."""
    total = sum(counts.values()) or 1
    lost = (counts.get("dropped", 0) + counts.get("weakened", 0)) / total
    primary = sum(1 for k in kinds if k == "primary")
    reasons, minus = [], 0
    if len(kinds) < 5:
        minus += 2
        reasons.append(f"only {len(kinds)} source{'s' if len(kinds) != 1 else ''} behind the report")
    if kinds and primary == 0:
        minus += 1
        reasons.append("no primary sources (maker, regulator, study or original test); all are second-hand")
    if lost > 0.3:
        minus += 2
        reasons.append(f"{round(lost * 100)}% of claims failed or were weakened in checking")
    elif lost > 0.15:
        minus += 1
        reasons.append(f"{round(lost * 100)}% of claims failed or were weakened in checking")
    if precision is not None and precision < 0.8:
        minus += 2
        reasons.append(f"only {round(precision * 100)}% of cited numbers were found in their sources")
    elif precision is not None and precision < 0.95:
        minus += 1
        reasons.append(f"{round(precision * 100)}% of cited numbers were found in their sources")
    if fact_problems > 5:
        minus += 1
        reasons.append(f"{fact_problems} checking problems were left unfixed in the text")
    if gaps > 3:
        minus += 1
        reasons.append(f"{gaps} gaps the research could not fill")
    read, blocked = stats.get("pages_read", 0), stats.get("pages_blocked", 0)
    if read and blocked > read:
        minus += 1
        reasons.append(f"{blocked} pages could not be read, more than were read")
    if unwritten:
        minus += 3
        reasons.append(f"{unwritten} part{'s' if unwritten != 1 else ''} of the report could not be written")
    if size == "small":
        minus += 1
        reasons.append("written by a small model")
    level = "good" if minus <= 1 else "fair" if minus <= 4 else "weak"
    if not reasons:
        reasons.append("claims held up in checking and the sources include primary ones")
    return {"level": level, "reasons": reasons}
