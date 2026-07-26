"""
TokenLedger - Lightweight governance layer for LLM applications.
Zero database required.
"""

from .core.ledger import TokenLedger
from .core.budget import BudgetExceededError
from .core.interceptor import UnknownModelError, CircuitBreakerOpenError
from .core.store import MemoryStore
from .ext.differentiators import (
    PromptCache, EstimatorFeedback, ModelRouter, RouteOption,
    CostContract, CostContractRegistry, PromptEvolutionTracker,
    LocalModelCost, LocalModelRegistry,
    simulate_cost, compute_roi, sign_ledger, verify_signed_ledger,
)

__version__ = "1.3.0"
__all__ = [
    "TokenLedger",
    "MemoryStore",
    "BudgetExceededError",
    "UnknownModelError",
    "CircuitBreakerOpenError",
    "PromptCache", "EstimatorFeedback", "ModelRouter", "RouteOption",
    "CostContract", "CostContractRegistry", "PromptEvolutionTracker",
    "LocalModelCost", "LocalModelRegistry",
    "simulate_cost", "compute_roi", "sign_ledger", "verify_signed_ledger",
]
