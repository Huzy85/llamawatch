"""Request history collector — recent LLM request log for llamawatch."""

WIDGET_ID = "request-history"
WIDGET_NAME = "Request History"
WIDGET_ICON = "\U0001f4dc"  # scroll
WIDGET_DESCRIPTION = "Recent LLM requests with timing and token counts"
WIDGET_DEFAULT_SIZE = {"w": 6, "h": 3, "minW": 4, "minH": 2}
WIDGET_REQUIRES = []
WIDGET_CONFIG_SCHEMA: list[dict] = []
WIDGET_CONFIG_REQUIRED = False
WIDGET_MULTI_INSTANCE = False

_log_instance = None


def _get_log():
    """Lazy singleton for the request log."""
    global _log_instance
    if _log_instance is None:
        from llamawatch.request_log import RequestLog
        _log_instance = RequestLog()
    return _log_instance


def collect(config=None, adapters=None, widget_config=None) -> dict:
    """Return recent requests for the widget."""
    try:
        log = _get_log()
        requests = log.get_recent(30)
    except Exception:
        requests = []
    return {"requests": requests}
