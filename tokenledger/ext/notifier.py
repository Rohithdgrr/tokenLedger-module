"""Webhook and notification helpers for TokenLedger budget events."""

import json
import logging
from typing import Any, Callable, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)


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
    ):
        self.slack_url = slack_url
        self.generic_url = generic_url
        self.timeout = timeout

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
            with urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    logger.warning("Slack webhook returned %s", resp.status)
        except URLError as e:
            logger.warning("Slack webhook failed: %s", e)

    def _post_generic(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        try:
            req = Request(self.generic_url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            with urlopen(req, timeout=self.timeout) as resp:
                if resp.status >= 400:
                    logger.warning("Generic webhook returned %s", resp.status)
        except URLError as e:
            logger.warning("Generic webhook failed: %s", e)

    def on_budget_exceeded(self, error: Any) -> None:
        msg = f"[TokenLedger] Budget exceeded: {error}"
        logger.warning(msg)
        self._post({"event": "budget_exceeded", "message": str(error)})

    def on_record(self, record: Dict[str, Any]) -> None:
        cost = record.get("cost_usd", 0)
        if cost > 0:
            self._post({
                "event": "record",
                "message": f"[TokenLedger] {record.get('model','?')} — "
                f"${cost:.6f}, {record.get('total_tokens',0)} tokens",
                "record": record,
            })

    def on_budget_threshold(
        self, scope: str, scope_id: str, current_spend: float, limit: float
    ) -> None:
        ratio = current_spend / limit if limit else 0
        msg = (
            f"[TokenLedger] Budget threshold: {scope}:{scope_id} "
            f"at {ratio:.1%} (${current_spend:.4f} / ${limit:.4f})"
        )
        logger.warning(msg)
        self._post({"event": "budget_threshold", "message": msg})
