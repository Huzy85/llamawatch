"""Optional relevance scoring with a cross-encoder reranker.

A reranker reads the question and a passage together and scores how well the
passage answers it. Dedicated rerankers judge relevance better and cheaper
than asking a chat model, so when one is configured the app uses it to order
search results and to pick which parts of a long page the model reads. The
result no longer depends on the size of the model the user picked.

Any server with the common rerank API works (llama.cpp `--reranking`, TEI,
Jina, Cohere-style): POST {"query", "documents"} and get back
{"results": [{"index", "relevance_score"}]}. Off unless research.reranker_url
is set. Three failures in a row switch it off for the rest of the run and the
app falls back to word matching.
"""

from __future__ import annotations

import logging
import threading

import httpx

log = logging.getLogger(__name__)

MAX_DOC_CHARS = 3000      # keeps each passage inside a 512 to 8K token reranker


def rerank_url(base: str) -> str:
    base = (base or "").rstrip("/")
    if not base or base.endswith("/rerank"):
        return base
    return base + ("/rerank" if base.endswith("/v1") else "/v1/rerank")


class Reranker:
    def __init__(self, url: str, model: str = "", api_key: str = "", timeout: float = 30,
                 covered: float = -5.0):
        self.url = rerank_url(url)
        self.covered = covered
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.failures = 0
        self.calls = 0
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg: dict) -> "Reranker | None":
        url = (cfg or {}).get("reranker_url", "")
        if not url:
            return None
        # no key from config: a local reranker needs none, and a key in a plain field would not be encrypted
        return cls(url, cfg.get("reranker_model", ""), covered=float(cfg.get("reranker_covered", -5.0)))

    @property
    def live(self) -> bool:
        return bool(self.url) and self.failures < 3

    def scores(self, query: str, docs: list[str]) -> list[float] | None:
        """One score per doc, higher is more relevant. None if the reranker is unavailable."""
        if not docs or not self.live:
            return None
        body = {"query": query, "documents": [d[:MAX_DOC_CHARS] for d in docs]}
        if self.model:
            body["model"] = self.model
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = httpx.post(self.url, json=body, headers=headers, timeout=self.timeout)
            r.raise_for_status()
            out = [float("-inf")] * len(docs)
            for item in r.json().get("results", []):
                out[int(item["index"])] = float(item.get("relevance_score", item.get("score", 0)))
            with self._lock:
                self.failures, self.calls = 0, self.calls + 1
            return out
        except Exception as e:  # network, bad JSON, wrong API shape
            with self._lock:
                self.failures += 1
            log.warning("reranker failed (%s): %s", self.url, e)
            return None
