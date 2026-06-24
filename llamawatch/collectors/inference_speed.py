"""Inference speed collector for LlamaWatch.

Tracks token throughput and latency by reading Prometheus metrics from
llama.cpp /metrics endpoints and computing per-poll deltas.
"""

WIDGET_ID = "inference-speed"
WIDGET_NAME = "Inference Speed"
WIDGET_ICON = "⚡"
WIDGET_DESCRIPTION = "Token throughput and latency tracking"
WIDGET_DEFAULT_SIZE = {"w": 4, "h": 2, "minW": 3, "minH": 2}
WIDGET_REQUIRES = ["backend"]
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_CONFIG_REQUIRED = False
WIDGET_MULTI_INSTANCE = False

import collections
import datetime
import json
import time
import urllib.request

# Ring buffer: last 50 tok/s readings for sparkline
_history: collections.deque = collections.deque(maxlen=50)

# /slots-based fallback: track per-(url, slot_id) tokens_decoded between polls
_prev_slot_tokens: dict = {}  # {(url, slot_id): (tokens_decoded, wall_time)}

# Previous Prometheus counter values keyed by adapter URL
# {url: {"llamacpp:tokens_predicted_total": float, ...}}
_prev_counters: dict = {}

# Timestamp of the last observed generation activity
_last_gen_time: float | None = None

# Ring buffer: last 100 TTFT values for percentile calculation
_ttft_history: collections.deque = collections.deque(maxlen=100)


# ---------------------------------------------------------------------------
# Prometheus parsing
# ---------------------------------------------------------------------------

def parse_prometheus_metrics(text: str) -> dict:
    """Parse Prometheus text-format metrics and return a dict of name → value.

    Handles:
    - Comment lines (starting with #) — skipped
    - Labels like metric{label="val"} value — label block stripped
    - Floating-point and integer values
    """
    result: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Split on whitespace; value is the last token (ignore optional timestamp)
        parts = line.split()
        if len(parts) < 2:
            continue
        raw_name = parts[0]
        raw_value = parts[1]

        # Strip label block if present: "metric{...}" → "metric"
        brace = raw_name.find("{")
        if brace != -1:
            raw_name = raw_name[:brace]

        try:
            result[raw_name] = float(raw_value)
        except ValueError:
            continue  # Non-numeric value — skip

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: float = 3.0) -> str | None:
    """GET a URL and return the response body, or None on any error."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def _compute_tps(adapter_url: str, current: dict) -> tuple[float | None, float | None]:
    """Compute generation and prompt tok/s by diffing current vs previous counters.

    Returns (generation_tps, prompt_tps). Returns None for a metric if there
    is no previous reading or the time delta is zero.
    """
    prev = _prev_counters.get(adapter_url)
    if prev is None:
        return None, None

    gen_tps: float | None = None
    prompt_tps: float | None = None

    # Generation: tokens / seconds
    tok_key = "llamacpp:tokens_predicted_total"
    sec_key = "llamacpp:tokens_predicted_seconds_total"
    if tok_key in current and sec_key in current and tok_key in prev and sec_key in prev:
        dtok = current[tok_key] - prev[tok_key]
        dsec = current[sec_key] - prev[sec_key]
        if dsec > 0:
            gen_tps = dtok / dsec

    # Prompt eval: tokens / seconds
    ptok_key = "llamacpp:prompt_tokens_total"
    psec_key = "llamacpp:prompt_seconds_total"
    if ptok_key in current and psec_key in current and ptok_key in prev and psec_key in prev:
        dptok = current[ptok_key] - prev[ptok_key]
        dpsec = current[psec_key] - prev[psec_key]
        if dpsec > 0:
            prompt_tps = dptok / dpsec

    return gen_tps, prompt_tps


def _compute_ttft(current: dict) -> float | None:
    """Compute average time-to-first-token in ms from cumulative counters, if available."""
    total_key = "llamacpp:time_to_first_token_ms_total"
    count_key = "llamacpp:time_to_first_token_ms_count"
    if total_key in current and count_key in current:
        count = current[count_key]
        if count > 0:
            return current[total_key] / count
    return None


def _update_history(adapter_url: str, current: dict) -> tuple[float | None, float | None]:
    """Compute gen_tps and prompt_tps, update _prev_counters and _history.

    Returns (gen_tps, prompt_tps). Stores *current* as the new baseline for
    the next poll before returning, so callers must not call _compute_tps again
    for the same adapter_url after this function runs.
    """
    global _last_gen_time

    # Capture prev BEFORE updating _prev_counters so TTFT recording can compare counts
    prev = _prev_counters.get(adapter_url, {})

    gen_tps, prompt_tps = _compute_tps(adapter_url, current)
    _prev_counters[adapter_url] = current

    if gen_tps is not None and gen_tps > 0:
        _history.append(gen_tps)
        _last_gen_time = time.time()

    # Record individual TTFT if a new request completed since last poll
    ttft = _compute_ttft(current)
    prev_count = prev.get("llamacpp:time_to_first_token_ms_count", 0)
    curr_count = current.get("llamacpp:time_to_first_token_ms_count", 0)
    if ttft is not None and curr_count > prev_count:
        _ttft_history.append(ttft)

    return gen_tps, prompt_tps


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------

def _percentile(data: list, pct: float) -> float | None:
    """Compute a percentile from a list of values."""
    if not data:
        return None
    sorted_data = sorted(data)
    idx = (pct / 100) * (len(sorted_data) - 1)
    lower = int(idx)
    upper = min(lower + 1, len(sorted_data) - 1)
    frac = idx - lower
    return round(sorted_data[lower] + frac * (sorted_data[upper] - sorted_data[lower]), 1)


# ---------------------------------------------------------------------------
# /slots-based fallback tok/s (used when /metrics is unavailable)
# ---------------------------------------------------------------------------

def _tps_from_slots(adapter_url: str) -> float | None:
    """Derive generation tok/s from delta of tokens_decoded across polls.

    llama.cpp /slots returns per-slot timings.predicted_per_second when a slot
    is actively generating.  If that field is present, use it directly.
    Otherwise fall back to tracking n_decoded deltas between polls.
    """
    global _last_gen_time
    url = adapter_url.rstrip("/")

    # Try bare /slots (works when there's only one model, or as default)
    body = _http_get(f"{url}/slots", timeout=2.0)
    if not body:
        return None
    try:
        slots = json.loads(body)
    except Exception:
        return None
    if not isinstance(slots, list):
        return None

    now = time.time()
    best_tps: float | None = None

    for slot in slots:
        if not slot.get("is_processing"):
            continue
        slot_id = slot.get("id", 0)

        # Prefer live timings.predicted_per_second if the server provides it
        tps_direct = slot.get("timings", {}).get("predicted_per_second")
        if tps_direct and tps_direct > 0:
            best_tps = max(best_tps or 0, tps_direct)
            _last_gen_time = now
            continue

        # Fallback: delta on n_decoded from next_token
        nt_list = slot.get("next_token")
        n_decoded: int | None = None
        if isinstance(nt_list, list) and nt_list:
            n_decoded = nt_list[0].get("n_decoded")
        if n_decoded is None:
            n_decoded = slot.get("n_decoded")
        if n_decoded is None:
            continue

        key = (url, slot_id)
        prev = _prev_slot_tokens.get(key)
        _prev_slot_tokens[key] = (n_decoded, now)

        if prev is not None:
            prev_tok, prev_time = prev
            dt = now - prev_time
            dtok = n_decoded - prev_tok
            if dt > 0 and dtok > 0:
                slot_tps = dtok / dt
                best_tps = max(best_tps or 0, slot_tps)
                _last_gen_time = now

    return best_tps if best_tps and best_tps > 0 else None


# ---------------------------------------------------------------------------
# Collector entry point
# ---------------------------------------------------------------------------

def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Collect inference speed metrics from all configured backend adapters.

    Uses the Prometheus /metrics endpoint exposed by llama.cpp to compute
    delta tok/s across polls. History is kept in a 50-entry ring buffer for
    sparkline rendering.

    Returns
    -------
    dict with keys:
        generation_tps          : float | None — tokens/sec for generation
        prompt_tps              : float | None — tokens/sec for prompt eval
        ttft_ms                 : float | None — avg time-to-first-token in ms
        history                 : list[float]  — last ≤50 generation tok/s values
        idle                    : bool         — True when no active generation
        idle_since              : str | None   — ISO timestamp of last generation end
        total_generation_tokens : int | None   — cumulative generation tokens
        total_prompt_tokens     : int | None   — cumulative prompt eval tokens
        ttft_p50                : float | None — TTFT 50th percentile (ms)
        ttft_p95                : float | None — TTFT 95th percentile (ms)
    """
    generation_tps: float | None = None
    prompt_tps: float | None = None
    ttft_ms: float | None = None

    if adapters is not None:
        all_adapters = adapters.get_all()
        for adapter in all_adapters:
            url = adapter.url.rstrip("/")
            metrics_url = f"{url}/metrics"
            body = _http_get(metrics_url, timeout=3.0)
            if body is None:
                continue

            current = parse_prometheus_metrics(body)
            if not current:
                continue

            gen_tps, p_tps = _update_history(url, current)

            # Use first adapter that has data
            if generation_tps is None and gen_tps is not None:
                generation_tps = gen_tps
            if prompt_tps is None and p_tps is not None:
                prompt_tps = p_tps
            if ttft_ms is None:
                ttft_ms = _compute_ttft(current)

    # Fallback: if /metrics gave nothing, try /slots-based derivation
    if generation_tps is None and adapters is not None:
        for adapter in adapters.get_all():
            slot_tps = _tps_from_slots(adapter.url)
            if slot_tps is not None:
                generation_tps = slot_tps
                _history.append(slot_tps)
                break

    idle = generation_tps is None or generation_tps <= 0
    idle_since: str | None = None
    if _last_gen_time is not None:
        idle_since = datetime.datetime.fromtimestamp(_last_gen_time).isoformat()

    # Cumulative token totals from first adapter that had metrics
    total_gen_tokens: int | None = None
    total_prompt_tokens: int | None = None
    for url_key, counters in _prev_counters.items():
        if total_gen_tokens is None:
            tok_val = counters.get("llamacpp:tokens_predicted_total")
            if tok_val is not None:
                total_gen_tokens = int(tok_val)
        if total_prompt_tokens is None:
            ptok_val = counters.get("llamacpp:prompt_tokens_total")
            if ptok_val is not None:
                total_prompt_tokens = int(ptok_val)
        if total_gen_tokens is not None and total_prompt_tokens is not None:
            break

    # Latency percentiles
    ttft_vals = list(_ttft_history)
    ttft_p50 = _percentile(ttft_vals, 50)
    ttft_p95 = _percentile(ttft_vals, 95)

    return {
        "generation_tps": generation_tps if (generation_tps is not None and generation_tps > 0) else None,
        "prompt_tps": prompt_tps if (prompt_tps is not None and prompt_tps > 0) else None,
        "ttft_ms": ttft_ms,
        "history": list(_history),
        "idle": idle,
        "idle_since": idle_since,
        "total_generation_tokens": total_gen_tokens,
        "total_prompt_tokens": total_prompt_tokens,
        "ttft_p50": ttft_p50,
        "ttft_p95": ttft_p95,
    }
