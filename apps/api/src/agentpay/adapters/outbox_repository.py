"""Transactional outbox repository adapter re-export (§106)."""

from __future__ import annotations

from agentpay.adapters.in_memory.outbox_repository import InMemoryOutboxRepository

__all__ = [
    "InMemoryOutboxRepository",
]
