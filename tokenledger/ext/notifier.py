"""Webhook and notification helpers for TokenLedger budget events."""

import json
import logging
import threading
import time
from typing import Any, Optional
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_HTTP_SCHEMES = ("http", "https")


def _validate_http_url(url: str, what: str) -> None:
    if urlparse(url).scheme not in _HTTP_SCHEMES:
        raise ValueError(f"{what} must be an http(s) URL, got: {url}")


class WebhookNotifier:
    """Sends notifications via webhook URLs when budget events fire.

    Usage:
        notifier = WebhookNotifier(slack_url="https://hooks.slack.com/...")
        ledger.interceptor.on_budget_exceeded = notifier.on_budget_exceeded
        ledger.interceptor.on_record = notifier.on_record
    """

    def __init__(
        self,
        slack_url: Optional[str] = None,
        generic_url: Optional[str] = None,
        timeout: float = 5.0,
        batch_size: int = 10,
        flush_interval: float = 5.0,
        throttle_interval: float = 0.0,
    ):
        self.slack_url = slack_url
        self.generic_url = generic_url
        self.timeout = timeout
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.throttle_interval = throttle_interval
        self._batch: list[dict[str, Any]] = []
        self._batch_lock = threading.Lock()
        self._batch_timer: Optional[threading.Timer] = None
        self._last_send = 0.0
        if slack_url:
            _validate_http_url(slack_url, "slack_url")
        if generic_url:
            _validate_http_url(generic_url, "generic_url")

    def _post(self, payload: dict) -> None:
        if self.slack_url:
            self._post_slack(payload)
        if self.generic_url:
            self._post_generic(payload)

    def _post_slack(self, payload: dict) -> None:
        body = json.dumps({"text": payload.get("message", "")}).encode()
        try:
            req = Request(self.slack_url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            with urlopen(req, timeout=self.timeout) as resp:  # nosec B310: scheme restricted to http(s) in __init__
                if resp.status != 200:
                    logger.warning("Slack webhook returned %s", resp.status)
        except URLError as e:
            logger.warning("Slack webhook failed: %s", e)

    def _post_generic(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        try:
            req = Request(self.generic_url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            with urlopen(req, timeout=self.timeout) as resp:  # nosec B310: scheme restricted to http(s) in __init__
                if resp.status >= 400:
                    logger.warning("Generic webhook returned %s", resp.status)
        except URLError as e:
            logger.warning("Generic webhook failed: %s", e)

    def on_budget_exceeded(self, error: Any) -> None:
        msg = f"[TokenLedger] Budget exceeded: {error}"
        logger.warning(msg)
        self._post({"event": "budget_exceeded", "message": str(error)})

    def _flush_batch(self) -> None:
        with self._batch_lock:
            # Always release the fired timer reference so a throttled batch
            # can be rescheduled (and on_record can start a fresh timer).
            if self._batch_timer:
                self._batch_timer.cancel()
                self._batch_timer = None
            if not self._batch:
                return
            if self.throttle_interval and (time.monotonic() - self._last_send) < self.throttle_interval:
                # Too soon to send — keep the batch and reschedule instead of
                # dropping records on the floor.
                wait = self.throttle_interval - (time.monotonic() - self._last_send)
                self._batch_timer = threading.Timer(wait, self._flush_batch)
                self._batch_timer.daemon = True
                self._batch_timer.start()
                return
            batch, self._batch = self._batch, []
        self._last_send = time.monotonic()
        self._send_batch(batch)

    def _send_batch(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        if len(batch) == 1:
            r = batch[0]
            self._post(
                {
                    "event": "record",
                    "message": f"[TokenLedger] {r.get('model', '?')} — ${r.get('cost_usd', 0):.6f}, {r.get('total_tokens', 0)} tokens",
                    "record": r,
                }
            )
        else:
            self._post({"event": "batch", "count": len(batch), "records": batch})

    def on_record(self, record: dict[str, Any]) -> None:
        cost = record.get("cost_usd", 0)
        if cost == 0:
            return
        # Batch to avoid flooding at high throughput
        if self.batch_size <= 1:
            self._send_batch([record])
            return
        should_flush = False
        with self._batch_lock:
            self._batch.append(record)
            if len(self._batch) >= self.batch_size:
                if self._batch_timer:
                    self._batch_timer.cancel()
                    self._batch_timer = None
                should_flush = True
            elif self._batch_timer is None:
                self._batch_timer = threading.Timer(self.flush_interval, self._flush_batch)
                self._batch_timer.daemon = True
                self._batch_timer.start()
        if should_flush:
            # Route through _flush_batch so throttling applies here too.
            self._flush_batch()

    def on_budget_threshold(self, scope: str, scope_id: str, current_spend: float, limit: float) -> None:
        ratio = current_spend / limit if limit else 0
        msg = f"[TokenLedger] Budget threshold: {scope}:{scope_id} at {ratio:.1%} (${current_spend:.4f} / ${limit:.4f})"
        logger.warning(msg)
        self._post({"event": "budget_threshold", "message": msg})
