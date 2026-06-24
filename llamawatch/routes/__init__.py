"""HTTP/WebSocket route modules for llamawatch.

Each module exposes an ``APIRouter`` named ``router`` that ``server.py`` mounts.

Routes reach shared application state (``_config``, ``_adapters``,
``_collector_registry``), patchable dependencies, and shared helpers through the
``srv`` proxy defined here — e.g. ``srv._config``, ``srv.create_adapter(...)``,
``srv._pr_db()``. The proxy resolves ``llamawatch.server`` lazily on first
attribute access (request time), so importing a route module never triggers
``server.py`` initialisation and the import works in any order — no circular
import. Tests that patch ``llamawatch.server.*`` are unaffected because the
proxy reads/writes the live module attributes.
"""


class _ServerProxy:
    """Lazy attribute proxy onto the ``llamawatch.server`` module."""

    __slots__ = ()

    def __getattr__(self, name):
        import llamawatch.server as _s
        return getattr(_s, name)

    def __setattr__(self, name, value):
        import llamawatch.server as _s
        setattr(_s, name, value)


srv = _ServerProxy()
