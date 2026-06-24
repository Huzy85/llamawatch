"""Server-Sent Events generator. Reuses the hub's collect_all + compute_diff,
keeping its OWN snapshot so it never races the WebSocket hub's shared _last_state."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

# Dedicated executor so concurrent initial-paint collectors don't compete with
# the default ThreadPoolExecutor used elsewhere.  16 workers is well above the
# number of dashboard widgets (~20 max), so all fire in the first batch.
_COLLECT_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="llamawatch-sse")


async def event_stream(hub, config=None, adapters=None, enabled_ids=None, interval=None, max_iterations=None):
    loop = asyncio.get_running_loop()
    state = {}
    ids = list(enabled_ids) if enabled_ids else None
    if ids:
        # Concurrent initial paint: all widgets start at once.
        # Uses hub._registry.collect_one() which is read-only / thread-safe —
        # it never touches hub._registry._enabled (avoiding the shared-state
        # race present in the old per-widget hub.collect_all([w]) approach).
        async def _collect(wid):
            try:
                res = await asyncio.wait_for(
                    loop.run_in_executor(
                        _COLLECT_EXECUTOR,
                        lambda w=wid: hub._registry.collect_one(w, config, adapters),
                    ),
                    timeout=8,
                )
                return res or {}
            except Exception:
                return {}

        tasks = [asyncio.ensure_future(_collect(w)) for w in ids]
        for fut in asyncio.as_completed(tasks):
            partial = await fut
            for k, v in (partial or {}).items():
                state[k] = v
                yield {"event": "widget", "data": json.dumps({"id": k, "data": v})}
    else:
        state = await loop.run_in_executor(None, lambda: hub.collect_all(config, adapters, enabled_ids))
        yield {"event": "full", "data": json.dumps(state)}

    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        is_swapping = state.get("model-status", {}).get("status") == "swapping"
        await asyncio.sleep(interval if interval is not None else (2 if is_swapping else 5))
        new_state = await loop.run_in_executor(None, lambda: hub.collect_all(config, adapters, enabled_ids))
        diff = hub.compute_diff(state, new_state)
        for widget_id, widget_data in diff.items():
            yield {"event": "widget", "data": json.dumps({"id": widget_id, "data": widget_data})}
        state = new_state
        iterations += 1
