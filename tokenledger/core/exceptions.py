"""Centralized exception definitions for TokenLedger."""

from __future__ import annotations


class BudgetExceededError(Exception):
    """Raised when a budget limit is exceeded."""

    def __init__(
        self,
        message: str,
        scope: str = "",
        scope_id: str = "",
        current_spend: float = 0.0,
        limit: float = 0.0,
    ):
        super().__init__(message)
        self.scope = scope
        self.scope_id = scope_id
        self.current_spend = current_spend
        self.limit = limit


class WalletExhaustedError(BudgetExceededError):
    """Raised when a wallet debit would exceed the remaining allowance."""


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open for a provider."""


class UnknownModelError(Exception):
    """Raised when an unknown model is encountered and policy is 'block'."""
