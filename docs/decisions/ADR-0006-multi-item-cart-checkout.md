# ADR-0006: Multi-Item Carts as the Canonical Pre-Checkout Container

## Status

Accepted

## Context

The blueprint specifies a commerce cart in the domain model (§7) and agent-facing endpoints
`POST /agent/carts` and `POST /agent/checkout` (§38), and admonishes that the platform must not
hard-code artificial restrictions on legitimate merchant behavior (§37). The implemented vertical
slice, however, had no `Cart` domain model: `Order` is strictly single-variant, the ACP protocol
adapter collapsed multi-item carts into one single-variant `ActionRequest`, and `create_cart` was a
registered-but-unimplemented tool that fails closed. `docs/engineering/known-limitations.md`
described this gap as the "single-item cart" limitation.

The open design questions were:

1. What is a cart, and how does it relate to `ActionRequest`, approvals, payments, and orders?
2. Can a cart span multiple merchants?
3. How does a cart checkout move money without duplicating the single-variant purchase pipeline?

## Options Considered

1. **No cart model; keep collapsing carts to single-item actions.**
   *Rejected*: Contradicts §7/§38 and permanently blocks multi-item autonomous checkout.
2. **Multi-merchant cart with split payments per merchant.**
   *Rejected for now*: Requires partial captures, per-merchant approvals, and per-merchant sagas;
   none of the payment provider adapters support split settlement. This would multiply the
   compensation surface without a provider that can execute it.
3. **Single-merchant cart (MVP), one payment, N single-variant orders.**
   *Selected*: Preserves the existing Order invariant (single variant per order), reuses the
   audited saga shape (reserve-all → pay-once → confirm-all → compensate-all), and keeps approval
   binding on one amount the human can meaningfully review.

## Decision

Adopt a first-class `Cart` aggregate with these semantics:

- **Domain**: `Cart` (frozen dataclass) holds `cart_id`, `tenant_id`, `principal_id`, `agent_id`,
  `merchant_id | None`, `status` (`ACTIVE → CHECKED_OUT`, `ABANDONED`), and a tuple of
  `CartItem(variant_id, quantity)` with merge-on-add semantics. Quantities are positive ints.
- **Single merchant**: a cart binds to exactly one merchant. The merchant is resolved from the
  first item's variant→product chain and every later add must resolve to the same merchant;
  mixing merchants fails closed with a domain error. This is an explicit MVP constraint, not a
  permanent product boundary — multi-merchant carts require split payments (see Revisit When).
- **Same authorization surface**: cart creation and item updates are authorized through the
  existing deterministic `AgentAuthorizationService` with the canonical action names `cart`
  (capability `cart.create`) and `checkout` (capability `checkout.prepare`) — the mapping already
  existed in `DEFAULT_CAPABILITY_ACTION_MAP`; the demo seed agent gains `cart.create`.
- **Checkout as an ActionRequest**: `POST /agent/checkout` builds a canonical
  `ActionRequest(action="checkout", resource=ResourceRef(type="cart", id=cart_id))` whose
  `amount` is the caller-asserted total. The server recomputes the total from live variant
  prices; any mismatch is a `PRICE_MISMATCH` conflict and fails closed (§33). Per-item quantity
  above effective availability is an `INVENTORY_MISMATCH` conflict.
- **Policy on the cart total**: the deterministic spending policy evaluates the recomputed total
  once (per-transaction and daily limits). `REVIEW` produces a single approval bound (8-field
  binding, ADR-0004) to `(action, total, merchant, cart_id, currency, action_version, principal,
  tenant)`.
- **One payment, N orders**: on `ALLOW` (or approved execution) the pipeline reserves every item
  atomically-or-compensates, creates exactly one payment for the total, then persists one
  `CONFIRMED` single-variant `Order` per cart item, all sharing the one `payment_attempt_id`.
  Partial reserve failure, payment decline/failure, and order-persistence failure all release
  every reserved item (and refund if captured), mirroring the single-purchase saga (§44).
- **Idempotency**: checkout uses the same idempotency store and conflict checks; a replayed
  checkout returns the stored result (order ids, payment id, cart id) without moving money again.
- **Concurrency**: checkout of a given cart is serialized per `(tenant, cart_id)` by process-local
  striped re-entrant locks held across the decision → reservation → payment → `CHECKED_OUT`
  pipeline in both `execute_cart_purchase` and `execute_approved_cart_purchase`. Two concurrent
  checkouts with different idempotency keys cannot both pass the ACTIVE-cart check: the loser
  observes `CHECKED_OUT` and fails closed. The guard is process-local; a multi-process deployment
  additionally requires a database-level atomic claim on the cart.
- **Persistence**: new tables `carts` and `cart_items` (Alembic `0005_add_cart_tables`), tenant
  scoped on every repository method; in-memory and SQLAlchemy implementations satisfy one
  `CartRepository` port.

## Why

- Money still moves exactly once per checkout, through the one payment provider, with one
  compensatable saga — no new financial primitive is introduced.
- The approval a human reviews is one bound total, not N unbounded line items.
- `Order` stays single-variant: reporting, reconciliation, and refunds keep working unchanged.
- The cart is a container, not an authority: prices, availability, policy, and approval checks
  are all re-verified at checkout time (and again at approved-execution time).

## Consequences

- New domain module `domain/cart.py`; new port `CartRepository`; in-memory and SQLAlchemy
  adapters; migration `0005`; orchestrator methods `create_cart`, `add_cart_item`,
  `get_cart_payload`, `execute_cart_purchase`, `execute_approved_cart_purchase`.
- API: `POST /agent/carts`, `GET /agent/carts/{id}`, `POST /agent/carts/{id}/items`,
  `POST /agent/checkout`; `POST /api/v1/approvals/{id}/execute` dispatches to the cart path when
  the approval's bound resource resolves to a cart.
- `create_cart`/`prepare_checkout` tools remain non-executable on the tool surface by design;
  the canonical cart endpoints are the only mutating path (§26, §135).

## Alternatives Rejected

- **Cart items as separate action requests**: would require N approvals and N payments for one
  user intent — strictly worse for both humans and reconciliation.
- **Order schema generalized to line items now**: a breaking change to a stable, tested table
  (ADR guard: persistent data model change) without a product requirement it satisfies today.

## Security Impact

- Mixed-merchant carts fail closed (no ambiguous merchant binding).
- Checkout re-verifies price, availability, category permission, merchant lifecycle, and policy
  against ground truth; caller-asserted totals are never trusted.
- Cart, cart items, and cart approvals are tenant-scoped on every access; cross-tenant reads and
  checkouts are negative-tested.
- The cart cannot raise limits: capability checks and the policy engine are untouched.
- Concurrent checkouts of the same cart are serialized; duplicate payments and lost inventory
  updates from interleaved checkouts are regression-tested (`test_cart_flow.py` §9).

## Revisit When

- Payment providers support split settlement or partial capture across merchants.
- Order reporting requires line-item structure directly on orders.
- A product requirement emerges for persistent (long-lived) carts with expiry/notification.
- Deployment moves beyond a single process per tenant scope: replace the process-local striped
  locks with a database-level compare-and-set cart claim.
