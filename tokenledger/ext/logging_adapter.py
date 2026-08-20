"""
Logging adapter: emit every recorded usage as a structured log record.

``attach_log_handler(ledger)`` installs a callback on the ledger's
interceptor that logs each usage record via ``logging.getLogger("tokenledger.spend")``
with the record fields attached as ``extra`` (so format strings like
``%(provider)s %(model)s $%(cost_usd).6f`` work with ``LogRecord``
formatters). The previous ``on_record`` callback (e.g. a notifier or
metrics exporter) is preserved and chained.
"""

import logging
import weakref
from typing import Any, Callable, Optional

from ..core.ledger import TokenLedger

DEFAULT_LOGGER_NAME = "tokenledger.spend"

_PREVIOUS: "weakref.WeakKeyDictionary[TokenLedger, Optional[Callable[[dict[str, Any]], None]]]" = weakref.WeakKeyDictionary()

_FIELDS = (
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost_usd",
    "latency_ms",
    "status",
    "user_id",
    "project_id",
    "tenant_id",
    "conversation_id",
    "agent_id",
    "source",
    "timestamp",
)


def attach_log_handler(
    ledger: TokenLedger,
    logger_name: str = DEFAULT_LOGGER_NAME,
    level: int = logging.INFO,
) -> Callable[[dict[str, Any]], None]:
    """Install a structured-logging callback on ``ledger``.

    Returns the installed callback so it can be detached with
    :func:`detach_log_handler`. The callback chain is preserved: any
    previously installed ``on_record`` hook still runs after this one.
    """
    logger = logging.getLogger(logger_name)
    original = _PREVIOUS.get(ledger, ledger.interceptor.on_record)
    if ledger not in _PREVIOUS:
        _PREVIOUS[ledger] = original

    def _on_record(record: dict[str, Any]) -> None:
        extra: dict[str, Any] = {field: record.get(field) for field in _FIELDS if record.get(field) is not None}
        logger.log(level, "tokenledger usage", extra=extra)
        if original:
            original(record)

    ledger.interceptor.on_record = _on_record
    return _on_record


def detach_log_handler(
    ledger: TokenLedger,
    hook: Optional[Callable[[dict[str, Any]], None]] = None,
) -> None:
    """Remove a logging hook installed by :func:`attach_log_handler`.

    Pass the hook returned by :func:`attach_log_handler`, or omit it to
    detach whatever hook is currently installed on the ledger.
    """
    current = ledger.interceptor.on_record
    if hook is not None and current is not hook:
        return
    _sentinel: object = object()
    previous = _PREVIOUS.pop(ledger, _sentinel)  # type: ignore[arg-type]
    if previous is not _sentinel:
        ledger.interceptor.on_record = previous  # type: ignore[assignment]
    elif hook is not None:
        ledger.interceptor.on_record = None
