"""Plain-writing rules for the report and the social drafts.

Models get STYLE in every writing prompt. Models ignore some of it, so the app
also fixes what is safe to fix mechanically (dashes, curly quotes, filler
phrases) and lists the rest as problems for the one rewrite each section gets.

Only words that are almost never needed in a research report are banned.
Words with a plain technical meaning ("landscape" mode, a test "harness",
a software "ecosystem") are left alone, since a ban would flag good text.
"""

import re

STYLE = """Writing style:
- Write like a knowledgeable colleague, not a press release. Plain words: "is" not "serves as", "has" not "boasts".
- Never use these words: {banned}.
- No "not just X, but Y" or "it's not X, it's Y" sentences. Say the point directly.
- No lists of three padded adjectives. No "highlighting the importance of" style -ing phrases.
- No vague sources ("experts say", "studies suggest"). Name the source or leave it out.
- No opening throat-clearing ("In today's world", "It is worth noting that") and no upbeat closing line ("The future looks bright").
- No em dashes. Use a full stop, a comma or brackets.
- Mix short and long sentences. Use the specific number or name, not a vague phrase."""

BANNED = [
    "delve", "delves", "delving", "tapestry", "pivotal", "underscore", "underscores", "underscoring",
    "foster", "fosters", "fostering", "vibrant", "groundbreaking", "nestled", "showcasing", "showcases",
    "testament", "crucial", "garnering", "interplay", "intricate", "realm", "unleash", "unleashes",
    "embark", "bustling", "treasure trove", "seamlessly", "seamless", "holistic", "synergy",
    "paradigm", "game-changer", "game changer", "revolutionize", "revolutionise", "transformative",
    "empower", "empowers", "elevate", "elevates", "deep dive", "moat", "heavy lifting",
    "cutting-edge", "state-of-the-art", "unparalleled", "world-class", "boasts",
]

# phrase -> replacement, applied without asking the model
_FILLER = [
    (r"\bin order to\b", "to"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bserves as\b", "is"),
]
# phrases removed outright; the next word is capitalised when the phrase opened a sentence
_CUT = re.compile(r"(^|[.!?]\s+|\n|)(?:it is (?:important|worth|crucial) to note that|it is worth (?:noting|mentioning) that"
                  r"|it should be noted that|at the end of the day,?)\s+(\w)", re.I | re.M)

_WORD = re.compile(r"(?<![\w-])(" + "|".join(re.escape(w) for w in sorted(BANNED, key=len, reverse=True))
                   + r")(?![\w-])", re.I)
_NEG_PARALLEL = re.compile(r"\bnot (?:just|only|merely|simply)\b[^.!?\n]{1,80}?,?\s*(?:but|it'?s|it is)\b"
                           r"|\bit'?s not\b[^.!?\n]{1,60}?[,;]\s*it'?s\b", re.I)
_THROAT = re.compile(r"(?:^|(?<=[.!?]\s))(?:In today's [\w-]+ world|In recent years|It is clear that"
                     r"|There is no denying that|The future looks bright|Only time will tell"
                     r"|Exciting times ahead)", re.I | re.M)


# forms shown to the model; the checker also catches the -s/-ing forms in BANNED
_SHOWN = ["delve", "tapestry", "pivotal", "underscore", "foster", "vibrant", "groundbreaking", "nestled",
          "showcasing", "testament", "crucial", "garnering", "interplay", "intricate", "realm", "unleash",
          "embark", "bustling", "treasure trove", "seamless", "holistic", "synergy", "paradigm",
          "game-changer", "revolutionise", "transformative", "empower", "elevate", "deep dive", "moat",
          "heavy lifting", "cutting-edge", "state-of-the-art", "unparalleled", "world-class", "boasts"]


def prompt_rules() -> str:
    return STYLE.format(banned=", ".join(_SHOWN))


def tidy(text: str) -> str:
    """Safe mechanical fixes. Keeps citations, tables and Markdown intact."""
    t = text.replace("‘", "'").replace("’", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"\s*—\s*", ", ", t)                 # em dash -> comma
    t = re.sub(r"(?<=\w)\s+–\s+(?=\w)", ", ", t)   # spaced en dash used as a dash
    for pat, rep in _FILLER:
        t = re.sub(r"(^|[.!?]\s+|\n)?" + pat,
                   lambda m, rep=rep: (m.group(1) or "") + (rep.capitalize() if m.group(1) is not None or m.start() == 0 else rep),
                   t, flags=re.I | re.M)
    t = _CUT.sub(lambda m: m.group(1) + (m.group(2).upper() if m.group(1) or m.start() == 0 else m.group(2)), t)
    return re.sub(r",\s*,", ",", t)


def style_problems(text: str) -> list[str]:
    """What tidy() cannot fix. Each item reads as an instruction for a rewrite."""
    out = []
    words = sorted({m.group(1).lower() for m in _WORD.finditer(text)})
    if words:
        out.append("style: replace these words with plain ones: " + ", ".join(words))
    for m in _NEG_PARALLEL.finditer(text):
        out.append(f"style: say this directly, without the \"not just X but Y\" shape: \"{m.group(0)[:80]}\"")
    for m in _THROAT.finditer(text):
        out.append(f"style: cut the filler opening or closing \"{m.group(0)}\"")
    return out[:6]
