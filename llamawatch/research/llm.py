"""One way to call any model during a research run, local or paid.

A research model is either a backend llamawatch already monitors (local, free)
or an entry under research.models in config: any OpenAI-compatible API with a
key. Keys are stored as `api_key`, which the config layer encrypts on disk.

Paid runs carry a spending cap the user approved before the run. Every call
adds its token cost; once the cap is reached no further calls are made.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

_THINK = re.compile(r"<think>.*?</think>\s*", re.S)


class BudgetSpent(Exception):
    """The approved spending cap for this run has been reached."""


class ModelError(Exception):
    pass


@dataclass
class ModelSpec:
    name: str
    url: str                       # full chat/completions endpoint
    model: str
    api_key: str = ""
    price_in: float = 0.0          # per million input tokens, in the user's currency
    price_out: float = 0.0
    context: int = 32768           # tokens
    no_thinking: bool = False      # send enable_thinking: false (Qwen3 and similar)
    local: bool = True
    size: str = ""                 # small | medium | large, from config; otherwise read from the name

    @property
    def paid(self) -> bool:
        return self.price_in > 0 or self.price_out > 0


@dataclass
class Usage:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    seconds: float = 0.0
    by_stage: dict = field(default_factory=dict)


def chat_url(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def spec_from_config(entry: dict) -> ModelSpec:
    """research.models entry -> ModelSpec."""
    return ModelSpec(
        name=entry.get("name") or entry.get("model", "model"),
        url=chat_url(entry["base_url"]),
        model=entry["model"],
        api_key=entry.get("api_key", ""),
        price_in=float(entry.get("price_in", 0) or 0),
        price_out=float(entry.get("price_out", 0) or 0),
        context=int(entry.get("context", 32768)),
        no_thinking=bool(entry.get("disable_thinking", False)),
        local=not entry.get("api_key"),
        size=entry.get("size_class", ""),
    )


def spec_from_adapter(name: str, adapter) -> ModelSpec:
    """A backend llamawatch already watches. Local, so free."""
    cfg = getattr(adapter, "config", {}) or {}
    return ModelSpec(
        name=name,
        url=adapter.chat_completions_url(),
        model=adapter.model_name(),
        context=int(cfg.get("context") or cfg.get("context_window") or cfg.get("ctx_size") or 32768),
        no_thinking=bool(cfg.get("disable_thinking", False)),
        local=True,
        size=cfg.get("size_class", ""),
    )


class Model:
    def __init__(self, spec: ModelSpec, cap: float | None = None, usage: Usage | None = None,
                 timeout: float = 600):
        self.spec = spec
        self.cap = cap                 # None = no limit (local models)
        self.usage = usage or Usage()  # shared between models in one run
        self.timeout = timeout
        self._lock = threading.Lock()

    @property
    def chars_budget(self) -> int:
        """Rough characters of page text that fit, leaving room for the prompt
        and the answer. About 3 characters per token for English web text."""
        return max(6000, (self.spec.context - 6000) * 3)

    def chat(self, prompt: str, system: str = "", max_tokens: int = 2000,
             temperature: float = 0.3, stage: str = "") -> str:
        if self.cap is not None and self.usage.cost >= self.cap:
            raise BudgetSpent(f"spending cap {self.cap:.2f} reached")
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": prompt}]
        body = {"model": self.spec.model, "messages": messages, "stream": False,
                "max_tokens": max_tokens, "temperature": temperature}
        if self.spec.no_thinking:
            body["chat_template_kwargs"] = {"enable_thinking": False}
        headers = {"Content-Type": "application/json"}
        if self.spec.api_key:
            headers["Authorization"] = f"Bearer {self.spec.api_key}"
        t0 = time.time()
        data = None
        for attempt in range(4):
            try:
                r = httpx.post(self.spec.url, json=body, headers=headers, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(3 * (attempt + 1) ** 2)
                    continue
                if r.status_code >= 400:
                    raise ModelError(f"{self.spec.name}: http {r.status_code} {r.text[:200]}")
                data = r.json()
                break
            except (httpx.TransportError, ValueError) as e:
                if attempt == 3:
                    raise ModelError(f"{self.spec.name}: {e}") from e
                time.sleep(3 * (attempt + 1))
        if data is None:
            raise ModelError(f"{self.spec.name}: no answer")
        try:
            msg = data["choices"][0]["message"]
            text = msg.get("content") or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ModelError(f"{self.spec.name}: unexpected reply {str(data)[:200]}") from e
        text = _THINK.sub("", text).strip()
        if not text and (msg.get("reasoning_content") or msg.get("reasoning")):
            # the model used its whole answer allowance on hidden thinking
            raise ModelError(f"{self.spec.name}: thought but gave no answer; "
                             "turn thinking off for this model (disable_thinking)")
        u = data.get("usage") or {}
        tin = int(u.get("prompt_tokens") or len(prompt + system) // 3)
        tout = int(u.get("completion_tokens") or len(text) // 3)
        cost = (tin * self.spec.price_in + tout * self.spec.price_out) / 1_000_000
        with self._lock:
            us = self.usage
            us.calls += 1
            us.tokens_in += tin
            us.tokens_out += tout
            us.cost += cost
            us.seconds += time.time() - t0
            st = us.by_stage.setdefault(stage or "other", {"calls": 0, "tokens": 0, "cost": 0.0})
            st["calls"] += 1
            st["tokens"] += tin + tout
            st["cost"] += cost
        return text


# ── Cost estimate shown before a paid run ────────────────────────────
# Calls and tokens per depth, measured from test runs. Kept generous so the
# real cost lands under the estimate, not over it.
_EFFORT = {
    "quick": {"calls": 30,  "tokens_in": 200_000,   "tokens_out": 25_000},
    "full":  {"calls": 260, "tokens_in": 2_200_000, "tokens_out": 260_000},
}
_EFFORT["standard"] = _EFFORT["deep"] = _EFFORT["full"]     # names used before 2026-10


def estimate(spec: ModelSpec, depth: str) -> dict:
    e = _EFFORT.get(depth.lower(), _EFFORT["full"])
    cost = (e["tokens_in"] * spec.price_in + e["tokens_out"] * spec.price_out) / 1_000_000
    return {"model": spec.name, "depth": depth, "calls": e["calls"],
            "tokens": e["tokens_in"] + e["tokens_out"], "cost": round(cost, 2),
            "paid": spec.paid}


# ── Reading model output ─────────────────────────────────────────────
def sections(md: str) -> dict[str, str]:
    """Split Markdown into {heading lower-case: body} on ## headings."""
    out, cur, buf = {}, "_top", []
    for line in md.splitlines():
        m = re.match(r"^#{2,3}\s+(.+?)\s*#*\s*$", line)
        if m and line.startswith("## "):
            out[cur] = "\n".join(buf).strip()
            cur, buf = m.group(1).strip().lower(), []
        else:
            buf.append(line)
    out[cur] = "\n".join(buf).strip()
    return out


def bullets(text: str) -> list[str]:
    items = []
    for line in (text or "").splitlines():
        m = re.match(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)", line)
        if m:
            items.append(m.group(1).strip())
    return [i for i in items if i.lower() not in ("none", "n/a", "none.")]


def field_value(text: str, name: str) -> str:
    m = re.search(rf"^\s*\**{re.escape(name)}\**\s*:\s*(.+)$", text or "", re.I | re.M)
    return m.group(1).strip().strip("*").strip() if m else ""


def loose_json(text: str):
    """Parse the first JSON object or list in a reply, forgiving common slips."""
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    for open_, close in (("{", "}"), ("[", "]")):
        i, j = text.find(open_), text.rfind(close)
        if i >= 0 and j > i:
            chunk = text[i:j + 1]
            for attempt in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk)):
                try:
                    return json.loads(attempt)
                except ValueError:
                    continue
    return None
