"""
Per-user allowance wallets.

Wallets are prepaid spending limits layered on top of the existing
budget engine: :class:`Wallet.debit` estimates the cost of a request
against the user's remaining allowance and raises
:class:`WalletExhaustedError` when the allowance would be exceeded.
Unlike plain budgets, wallets expose a live ``balance()`` and can fire a
one-shot ``on_low_balance`` callback when the balance drops below a
fraction of the original limit.
"""

from typing import Any, Callable, Optional

from .exceptions import BudgetExceededError, WalletExhaustedError
from .ledger import TokenLedger

# Re-export for backwards compatibility
__all__ = ["WalletExhaustedError"]


class Wallet:
    """A per-user spending allowance that resets on a cycle."""

    def __init__(
        self,
        ledger: TokenLedger,
        user_id: str,
        limit_usd: float,
        reset_cycle: str = "daily",
        low_balance_threshold: float = 0.2,
        on_low_balance: Optional[Callable[["Wallet"], None]] = None,
    ):
        if limit_usd <= 0:
            raise ValueError("limit_usd must be positive")
        if not 0.0 < low_balance_threshold <= 1.0:
            raise ValueError("low_balance_threshold must be in (0, 1]")
        self.ledger = ledger
        self.user_id = user_id
        self.reset_cycle = reset_cycle
        self.low_balance_threshold = low_balance_threshold
        self.on_low_balance = on_low_balance
        self._low_alerted = False
        self.ledger.set_budget("user", user_id, limit_usd, reset_cycle)

    @property
    def limit(self) -> float:
        return float(self._budget().get("limit_usd", 0.0))

    def _budget(self) -> dict[str, Any]:
        budget = self.ledger.store.get_budget("user", self.user_id)
        if not budget:
            raise RuntimeError(f"wallet budget missing for user '{self.user_id}'")
        return budget

    def spend(self) -> float:
        """Current spend inside the wallet's reset window."""
        return float(self.ledger.budget_enforcer._calculate_current_spend(self._budget()))

    def balance(self) -> float:
        """Remaining allowance in USD."""
        return round(self.limit - self.spend(), 10)

    def _estimate(
        self,
        provider: str,
        model: str,
        messages: Optional[list[dict[str, str]]],
        input_tokens: int,
        output_tokens: int,
        max_tokens: Optional[int],
    ) -> float:
        return float(
            self.ledger.budget_enforcer._estimate_request_cost(
                provider, model, messages, input_tokens, output_tokens, max_tokens
            )
        )

    def debit(
        self,
        provider: str,
        model: str,
        messages: Optional[list[dict[str, str]]] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        max_tokens: Optional[int] = None,
    ) -> bool:
        """Reserve the estimated cost of a request against the allowance.

        Raises :class:`WalletExhaustedError` when the request would push
        spend past the limit; returns ``True`` otherwise. Fires the
        one-shot ``on_low_balance`` callback the first time the balance
        drops below ``low_balance_threshold * limit``.
        """
        budget = self._budget()
        spend = self.spend()
        est = self._estimate(provider, model, messages, input_tokens, output_tokens, max_tokens)
        limit = float(budget.get("limit_usd", 0.0))
        if est > limit - spend:
            raise WalletExhaustedError(
                message=(
                    f"Wallet exhausted for {self.user_id}: "
                    f"${spend:.4f} + ${est:.4f} > ${limit:.4f}"
                ),
                scope="user",
                scope_id=self.user_id,
                current_spend=spend,
                limit=limit,
            )
        remaining = limit - spend - est
        if not self._low_alerted and remaining < self.low_balance_threshold * limit:
            self._low_alerted = True
            if self.on_low_balance:
                self.on_low_balance(self)
        return True

    def refill(self, amount: float) -> float:
        """Top up the allowance and return the new limit."""
        if amount <= 0:
            raise ValueError("refill amount must be positive")
        new_limit = round(self.limit + amount, 10)
        self.ledger.set_budget("user", self.user_id, new_limit, self.reset_cycle)
        if self.balance() >= self.low_balance_threshold * new_limit:
            self._low_alerted = False
        return new_limit

    def reset(self) -> None:
        """Reset the cycle and re-arm the low-balance alert."""
        self.ledger.set_budget("user", self.user_id, self.limit, self.reset_cycle)
        self._low_alerted = False
