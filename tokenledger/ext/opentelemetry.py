"""OpenTelemetry integration for TokenLedger.

Usage:
    from tokenledger.ext.opentelemetry import instrument_ledger
    instrument_ledger(ledger, tracer=my_tracer)
"""

from typing import Any, Callable, Dict, Optional

try:
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, Tracer

    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False
    SpanKind = None

    class Tracer:  # type: ignore
        pass


def instrument_ledger(
    ledger: Any,
    tracer: Optional[Tracer] = None,
    span_prefix: str = "tokenledger",
    record_attributes: bool = True,
) -> Any:
    """Wrap ledger's interceptor to emit OpenTelemetry spans on each tracked request.

    Args:
        ledger: TokenLedger instance.
        tracer: OpenTelemetry Tracer (uses global tracer provider if None).
        span_prefix: Prefix for span names (default "tokenledger").
        record_attributes: Attach token/cost attributes to spans.

    Returns:
        The same ledger instance (mutated in place).
    """
    if not _HAS_OTEL:
        raise ImportError(
            "OpenTelemetry is not installed. Add `tokenledger[opentelemetry]`."
        )

    if tracer is None:
        tracer = trace.get_tracer(__name__)

    interceptor = ledger.interceptor
    original_on_record = interceptor.on_record

    def _on_record(record: Dict[str, Any]) -> None:
        with tracer.start_as_current_span(
            f"{span_prefix}.record",
            kind=SpanKind.INTERNAL if SpanKind else None,
        ) as span:
            if record_attributes:
                span.set_attribute(f"{span_prefix}.model", record.get("model", ""))
                span.set_attribute(f"{span_prefix}.provider", record.get("provider", ""))
                span.set_attribute(
                    f"{span_prefix}.input_tokens", record.get("input_tokens", 0)
                )
                span.set_attribute(
                    f"{span_prefix}.output_tokens", record.get("output_tokens", 0)
                )
                span.set_attribute(
                    f"{span_prefix}.total_tokens", record.get("total_tokens", 0)
                )
                span.set_attribute(f"{span_prefix}.cost_usd", record.get("cost_usd", 0.0))
                span.set_attribute(f"{span_prefix}.user_id", record.get("user_id", ""))
                span.set_attribute(
                    f"{span_prefix}.project_id", record.get("project_id", "")
                )
                status = record.get("status", "success")
                span.set_attribute(f"{span_prefix}.status", status)
        if original_on_record:
            original_on_record(record)

    interceptor.on_record = _on_record
    return ledger
