"""Application port for shopping cart persistence and lookup (§38, ADR-0006).

Following Clean Architecture (§8, §10), every method strictly requires
an explicit `tenant_id` partition parameter (§49, §50).
"""

from __future__ import annotations

from agentpay.application.ports.commerce_port import CartRepository
from agentpay.domain.cart import Cart, CartItem, CartStatus
from agentpay.domain.errors import CartAlreadyCheckedOutError

__all__ = [
    "Cart",
    "CartAlreadyCheckedOutError",
    "CartItem",
    "CartRepository",
    "CartStatus",
]
