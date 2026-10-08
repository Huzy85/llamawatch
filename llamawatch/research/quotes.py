"""Check that a quote a model gave really appears in the stored page.

Runs in code, no model, so it works the same whatever model did the reading.
A quote the page does not contain is treated as invented and dropped.
"""

from __future__ import annotations

import re
import unicodedata

_SUBS = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ", " ": " ",
}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    for a, b in _SUBS.items():
        text = text.replace(a, b)
    text = re.sub(r"[*_`#>|\[\]]", " ", text)          # markdown noise
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:[.,][0-9]+)*", normalise(text))


def quote_score(quote: str, page: str) -> float:
    """1.0 = found word for word (ignoring case, spacing, quote marks).

    Otherwise the share of the quote's words found as one unbroken run in
    the page, so a quote with a trimmed word scores near 1 and a made-up
    sentence that merely shares some words scores low. Numbers must match
    exactly: a quote is only as good as its numbers.
    """
    q, p = normalise(quote), normalise(page)
    if not q:
        return 0.0
    if q in p:
        return 1.0
    qt, pt = _tokens(quote), _tokens(page)
    if not qt or not pt:
        return 0.0
    nums = [t for t in qt if re.search(r"\d", t)]
    page_set = set(pt)
    if any(n not in page_set for n in nums):
        return 0.0
    # longest run of quote tokens appearing consecutively in the page
    pos: dict[str, list[int]] = {}
    for i, t in enumerate(pt):
        pos.setdefault(t, []).append(i)
    best = 0
    for start in range(len(qt)):
        for p0 in pos.get(qt[start], ()):
            k = 1
            while start + k < len(qt) and p0 + k < len(pt) and pt[p0 + k] == qt[start + k]:
                k += 1
            best = max(best, k)
        if best >= len(qt) - start:
            break
    return best / len(qt)


def quote_found(quote: str, page: str, threshold: float = 0.85) -> bool:
    return quote_score(quote, page) >= threshold


def context(quote: str, page: str, chars: int = 600) -> str:
    """The quote with some surrounding page text, for the fact-checker."""
    q = normalise(quote)[:60]
    flat = re.sub(r"\s+", " ", page)
    i = normalise(flat).find(q) if q else -1
    if i < 0:
        return quote
    a, b = max(0, i - chars // 2), min(len(flat), i + len(quote) + chars // 2)
    return ("..." if a else "") + flat[a:b] + ("..." if b < len(flat) else "")
