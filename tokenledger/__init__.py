"""
TokenLedger - Lightweight governance layer for LLM applications.
Zero database required.
"""

from .core.budget import BudgetExceededError
from .core.interceptor import CircuitBreakerOpenError, UnknownModelError
from .core.ledger import TokenLedger
from .core.store import MemoryStore
from .core.wallet import Wallet, WalletExhaustedError
from .ext.autodetect import auto_detect
from .ext.differentiators import (
    CostContract,
    CostContractRegistry,
    EstimatorFeedback,
    LocalModelCost,
    LocalModelRegistry,
    ModelRouter,
    PromptCache,
    PromptEvolutionTracker,
    RouteOption,
    compute_roi,
    sign_ledger,
    simulate_cost,
    verify_signed_ledger,
)
from .ext.live_server import LiveServer
from .ext.logging_adapter import attach_log_handler, detach_log_handler

__version__ = "1.5.0"
__all__ = [
    "TokenLedger",
    "MemoryStore",
    "BudgetExceededError",
    "Wallet",
    "WalletExhaustedError",
    "UnknownModelError",
    "CircuitBreakerOpenError",
    "LiveServer",
    "attach_log_handler",
    "detach_log_handler",
    "PromptCache", "EstimatorFeedback", "ModelRouter", "RouteOption",
    "CostContract", "CostContractRegistry", "PromptEvolutionTracker",
    "LocalModelCost", "LocalModelRegistry",
    "simulate_cost", "compute_roi", "sign_ledger", "verify_signed_ledger",
    "auto_detect",
]
