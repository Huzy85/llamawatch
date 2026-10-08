"""Auto-discovery collector registry with config schemas and multi-instance support."""
import importlib
import pkgutil
from pathlib import Path
from typing import Sequence

from ..config import get_widget_config

_SKIP_MODULES = {"__init__"}

_SENTINEL = object()  # used to distinguish "no arg" from an explicit empty list


class CollectorRegistry:
    def __init__(self, config: dict | None = None):
        self._collectors = {}
        self._enabled: list[str] | None = None
        self._widget_configs: dict = {}
        self._config: dict = config or {}
        self._discover()
        if config is not None:
            self._apply_config(config)

    def _discover(self):
        pkg_dir = Path(__file__).parent
        for _, name, _ in pkgutil.iter_modules([str(pkg_dir)]):
            if name in _SKIP_MODULES:
                continue
            try:
                mod = importlib.import_module(f".{name}", package="llamawatch.collectors")
                if hasattr(mod, "WIDGET_ID") and hasattr(mod, "collect"):
                    self._collectors[mod.WIDGET_ID] = mod
            except Exception:
                continue

    def _apply_config(self, config: dict) -> None:
        self._config = config
        widgets = config.get("widgets", {})
        self._enabled = widgets.get("enabled", None)
        self._widget_configs = widgets.get("config", {})

    # ------------------------------------------------------------------
    # Multi-instance helpers
    # ------------------------------------------------------------------

    def _base_widget_id(self, instance_id: str) -> str:
        """Extract the base widget ID from an instance ID.

        'weather:abc123' -> 'weather', 'system' -> 'system'
        """
        return instance_id.split(":")[0]

    def _is_multi_instance(self, widget_id: str) -> bool:
        """Check if a collector supports multiple instances."""
        mod = self._collectors.get(widget_id)
        if mod is None:
            return False
        return getattr(mod, "WIDGET_MULTI_INSTANCE", False)

    # ------------------------------------------------------------------
    # Hot-reload
    # ------------------------------------------------------------------

    def refresh_enabled(self, config: dict) -> None:
        """Update the enabled widget list from a new config dict in-place."""
        self._apply_config(config)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_available_ids(self) -> list[str]:
        return list(self._collectors.keys())

    def get_enabled(self, enabled_ids: Sequence[str] | object = _SENTINEL) -> list:
        """Return enabled instance IDs (strings), filtering out invalid
        multi-instance duplicates for single-instance widgets.

        Parameters
        ----------
        enabled_ids:
            Optional explicit list of widget/instance IDs to filter by.
            When omitted the list stored via ``__init__(config)`` or
            ``refresh_enabled()`` is used.  Pass an explicit list for
            backward-compatible call sites.
        """
        if enabled_ids is _SENTINEL:
            ids = self._enabled if self._enabled is not None else list(self._collectors.keys())
        else:
            ids = list(enabled_ids)  # type: ignore[arg-type]

        result: list[str] = []
        seen_single: set[str] = set()

        for instance_id in ids:
            base_id = self._base_widget_id(instance_id)
            if base_id not in self._collectors:
                continue

            if self._is_multi_instance(base_id):
                # Multi-instance: allow all instances
                result.append(instance_id)
            else:
                # Single-instance: only keep the first occurrence
                if base_id not in seen_single:
                    seen_single.add(base_id)
                    result.append(base_id)

        return result

    def get_collector(self, widget_id: str):
        """Return the collector module for a base widget ID."""
        return self._collectors.get(widget_id)

    def get_manifest(self) -> list[dict]:
        """Return metadata for all discovered collectors."""
        manifest = []
        for mod in self._collectors.values():
            entry = {
                "id": mod.WIDGET_ID,
                "name": mod.WIDGET_NAME,
                "defaultSize": getattr(mod, "WIDGET_DEFAULT_SIZE", {"w": 4, "h": 2}),
                "requires": getattr(mod, "WIDGET_REQUIRES", []),
                "icon": getattr(mod, "WIDGET_ICON", "📦"),
                "description": getattr(mod, "WIDGET_DESCRIPTION", ""),
                "config_schema": getattr(mod, "WIDGET_CONFIG_SCHEMA", []),
                "multi_instance": getattr(mod, "WIDGET_MULTI_INSTANCE", False),
            }
            # Optional fields — only include when True
            if getattr(mod, "WIDGET_CONFIG_REQUIRED", False):
                entry["config_required"] = True
            if getattr(mod, "WIDGET_CREDENTIALS_REQUIRED", False):
                entry["credentials_required"] = True
                entry["credentials_help"] = getattr(mod, "WIDGET_CREDENTIALS_HELP", "")
            manifest.append(entry)
        return manifest

    # ------------------------------------------------------------------
    # Collection
    # ------------------------------------------------------------------

    def collect_one(self, widget_id: str, config=None, adapters=None) -> dict:
        """Collect a single widget instance by id without mutating shared _enabled.

        Safe to call concurrently from multiple threads — it reads registry
        state but never writes _enabled or any other shared attribute.

        Returns ``{widget_id: data}`` on success or ``{widget_id: {}}`` on
        failure.  The returned key is always the original *widget_id* passed
        in (which may be a multi-instance id like ``weather:abc123``).
        """
        cfg = config or self._config
        base_id = self._base_widget_id(widget_id)
        mod = self._collectors.get(base_id)
        if mod is None:
            return {widget_id: {}}
        try:
            widget_config = get_widget_config(cfg, widget_id)
            result = mod.collect(cfg, adapters, widget_config=widget_config)
        except TypeError:
            # Collector doesn't accept widget_config yet — call without it
            try:
                result = mod.collect(cfg, adapters)
            except Exception:
                result = {}
        except Exception:
            result = {}
        return {widget_id: result}

    def collect_all(self, config=None, adapters=None) -> dict:
        """Run enabled collectors and return a dict keyed by instance ID.

        For multi-instance widgets, collect() is called once per instance
        with instance-specific config via widget_config.  For single-instance
        widgets, collect() is called once and the result is stored under
        the base widget ID.
        """
        cfg = config or self._config
        result = {}
        enabled = self.get_enabled()

        for instance_id in enabled:
            base_id = self._base_widget_id(instance_id)
            mod = self._collectors.get(base_id)
            if mod is None:
                continue
            try:
                widget_config = get_widget_config(cfg, instance_id)
                result[instance_id] = mod.collect(cfg, adapters, widget_config=widget_config)
            except TypeError:
                # Collector doesn't accept widget_config yet — call without it
                try:
                    result[instance_id] = mod.collect(cfg, adapters)
                except Exception:
                    result[instance_id] = {}
            except Exception:
                result[instance_id] = {}

        return result
