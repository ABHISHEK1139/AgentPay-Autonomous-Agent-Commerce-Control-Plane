# ADR-0001: Start as a modular monolith, not microservices

## Status

Accepted

## Context

AgentPay is described as a platform, and blueprint §8 anticipates extracting services later for
procurement, travel, cloud, and logistics domains. The natural first move for a new "platform"
repository is to create many services so each domain is isolated from the start.

At Sprint 0 there is no code, no traffic profile, no scaling data, no team topology, and no
operating experience with the domain. Nothing has been measured yet.

## Problem

How do we get platform-grade boundaries now without paying the operational cost of distributed
systems before we know what the boundaries should be?

## Options

1. Microservices from the start — one service per domain, network calls between them.
2. Modular monolith with explicit in-process boundaries; extract later only on evidence.
3. Single unstructured application with no enforced layering.

## Decision

Option 2. `agentpay.domain` depends on nothing else in the codebase and nothing in any framework.
Adapters implement ports declared by the inner layers. AI code reaches the application layer only
through explicit interfaces and never writes to the database directly.

## Why

Distributed systems impose costs that are permanent and costs that are reversible. Serialization
boundaries, partial failure, distributed transactions, and per-service observability are all
expensive and hard to remove. Boundary *discipline* inside one process is nearly free and
preserves the option to extract later, because the seams already exist where they would be cut.

Option 3 is rejected because the absence of enforced layering is precisely what makes a codebase
impossible to extract from later: the dependencies are already tangled.

## Consequences

- A single deployable, single process, single migration history at first.
- Boundaries are enforced by tests, not by deployment topology — see
  `apps/api/tests/unit/test_architecture_boundaries.py`.
- Cross-domain work goes through the application layer, which is more indirection than a direct
  function call and is the point.
- Scaling one domain independently is not possible until it is extracted. That is a real cost,
  accepted until measurement justifies paying it.

## Alternatives rejected

- Microservices now: pays permanent distributed-systems cost against zero measured need, and makes
  the security boundaries harder to verify, since in-process assertions become network trust.
- A shared library of common types across services: tends to become a coupling layer that blocks
  independent evolution of every consumer.

## Security impact

In-process calls are auditable and enforceable. Splitting a domain across a network boundary turns
an `assert` into a trust decision. Keeping the monolith first keeps authorization and tenant
isolation verifiable by exhaustive unit tests.

## Revisit when

Measured evidence shows one domain has scaling characteristics, isolation requirements, or
ownership needs that genuinely differ from the rest — not before.
