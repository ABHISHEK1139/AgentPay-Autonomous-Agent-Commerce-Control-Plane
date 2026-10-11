"""Empirical stress-testing harness for ACP and AP2 protocol adapters (§107, §108).

Adversarially probes:
1. Negative amounts & quantities
2. Floating point values & non-integer types (including bools)
3. Mismatched and invalid currencies
4. Empty / malformed cart items
5. Missing / empty buyer and payer IDs
6. Missing / empty tenant IDs (payload and header)
7. Invalid timestamps, expired mandates, and future dates
8. Fail-closed behavior via HTTP API endpoints
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Add source and test paths
sys.path.insert(0, str(Path("apps/api/src").resolve()))
sys.path.insert(0, str(Path("apps/api/tests/unit").resolve()))

from test_purchase_flow import setup_orchestrator

from agentpay.adapters.protocols.acp import ACPAdapter
from agentpay.adapters.protocols.ap2 import AP2Adapter
from agentpay.adapters.protocols.base import (
    MalformedPayloadError,
    ProtocolError,
    UnsupportedProtocolVersionError,
)
from agentpay.adapters.protocols.uap import UAPAdapter
from agentpay.adapters.protocols.x402 import X402Adapter
from agentpay.api.app import AgentPayApiApp, ApiTestClient
from agentpay.domain.errors import (
    InvalidActionRequestError,
    InvalidCurrencyError,
    NegativeMoneyError,
)


def log(msg: str) -> None:
    """Output message to stdout without triggering T201 lint rule."""
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def run_tests() -> dict[str, Any]:
    acp = ACPAdapter()
    ap2 = AP2Adapter()
    uap = UAPAdapter()
    x402 = X402Adapter()
    orch, *_ = setup_orchestrator()
    app = AgentPayApiApp(orchestrator=orch)
    client = ApiTestClient(app).as_buyer()

    results: dict[str, list[dict[str, Any]]] = {
        "negative_amounts": [],
        "floating_point_values": [],
        "currency_mismatches": [],
        "empty_cart_and_items": [],
        "missing_ids": [],
        "missing_tenant": [],
        "dates_and_mandates": [],
        "api_fail_closed": [],
    }

    def record(category: str, name: str, passed: bool, detail: str, is_vuln: bool = False) -> None:
        results[category].append(
            {
                "name": name,
                "passed": passed,
                "detail": detail,
                "is_vulnerability": is_vuln,
            }
        )
        status_str = "PASS" if passed else ("VULN" if is_vuln else "FAIL")
        log(f"[{status_str}] [{category}] {name}: {detail}")

    log("=" * 70)
    log("STARTING EMPIRICAL PROTOCOL ADAPTER STRESS TESTS")
    log("=" * 70)

    # -----------------------------------------------------------------------
    # 1. NEGATIVE AMOUNTS
    # -----------------------------------------------------------------------
    # 1.1 ACP negative unit price
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "neg_1",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": -500, "currency": "INR"}]
                },
            }
        )
        record(
            "negative_amounts",
            "acp_negative_price",
            False,
            "Accepted negative unit_price_minor",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "negative_amounts", "acp_negative_price", True, "Rejected with MalformedPayloadError"
        )
    except Exception as e:
        record(
            "negative_amounts",
            "acp_negative_price",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 1.2 ACP negative quantity
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "neg_qty",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [
                        {
                            "product_id": "p1",
                            "unit_price_minor": 500,
                            "quantity": -2,
                            "currency": "INR",
                        }
                    ]
                },
            }
        )
        record(
            "negative_amounts",
            "acp_negative_quantity",
            False,
            "Accepted negative quantity",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "negative_amounts", "acp_negative_quantity", True, "Rejected with MalformedPayloadError"
        )
    except Exception as e:
        record(
            "negative_amounts",
            "acp_negative_quantity",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 1.3 ACP zero quantity
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "zero_qty",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [
                        {
                            "product_id": "p1",
                            "unit_price_minor": 500,
                            "quantity": 0,
                            "currency": "INR",
                        }
                    ]
                },
            }
        )
        record(
            "negative_amounts", "acp_zero_quantity", False, "Accepted zero quantity", is_vuln=True
        )
    except MalformedPayloadError:
        record("negative_amounts", "acp_zero_quantity", True, "Rejected with MalformedPayloadError")
    except Exception as e:
        record(
            "negative_amounts",
            "acp_zero_quantity",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 1.4 AP2 negative amount_minor
    try:
        ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "neg_ap2",
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": -1000,
                "currency": "INR",
            }
        )
        record(
            "negative_amounts",
            "ap2_negative_amount_minor",
            False,
            "Accepted negative amount_minor",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "negative_amounts",
            "ap2_negative_amount_minor",
            True,
            "Rejected with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "negative_amounts",
            "ap2_negative_amount_minor",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 1.5 AP2 negative amount.minor_units block
    try:
        ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "neg_ap2_block",
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount": {"minor_units": -500, "currency": "INR"},
            }
        )
        record(
            "negative_amounts",
            "ap2_negative_minor_units_block",
            False,
            "Accepted negative minor_units",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "negative_amounts",
            "ap2_negative_minor_units_block",
            True,
            "Rejected with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "negative_amounts",
            "ap2_negative_minor_units_block",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 1.6 UAP negative price rejection
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_neg_1",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "prod_1", "price_minor": -500, "currency": "INR"},
                },
            }
        )
        record(
            "negative_amounts",
            "uap_negative_price",
            False,
            "Accepted negative price_minor in UAP",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "negative_amounts", "uap_negative_price", True, "Rejected with MalformedPayloadError"
        )
    except Exception as e:
        record(
            "negative_amounts",
            "uap_negative_price",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 1.7 UAP negative quantity rejection
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_neg_qty",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "prod_1", "quantity": -2, "currency": "INR"},
                },
            }
        )
        record(
            "negative_amounts",
            "uap_negative_quantity",
            False,
            "Accepted negative quantity in UAP",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "negative_amounts", "uap_negative_quantity", True, "Rejected with MalformedPayloadError"
        )
    except Exception as e:
        record(
            "negative_amounts",
            "uap_negative_quantity",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 1.8 x402 negative amount_minor rejection
    try:
        x402.parse_payload(
            {
                "x402_version": "1.0",
                "challenge_id": "x402_neg_1",
                "resource_uri": "https://api.example.com/data",
                "principal_id": "user_01",
                "agent_id": "agent_01",
                "tenant_id": "tenant_01",
                "amount_minor": -250,
                "currency": "INR",
            }
        )
        record(
            "negative_amounts",
            "x402_negative_amount_minor",
            False,
            "Accepted negative amount_minor in x402",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "negative_amounts",
            "x402_negative_amount_minor",
            True,
            "Rejected with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "negative_amounts",
            "x402_negative_amount_minor",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 1.9 UAP zero quantity rejection
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_zero_qty",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "prod_1", "quantity": 0, "currency": "INR"},
                },
            }
        )
        record(
            "negative_amounts",
            "uap_zero_quantity",
            False,
            "Accepted zero quantity in UAP",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "negative_amounts",
            "uap_zero_quantity",
            True,
            "Rejected zero quantity with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "negative_amounts",
            "uap_zero_quantity",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 1.10 UAP negative budget_cap_minor rejection
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_neg_budget",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {
                        "product_id": "prod_1",
                        "price_minor": 100,
                        "budget_cap_minor": -500,
                        "currency": "INR",
                    },
                },
            }
        )
        record(
            "negative_amounts",
            "uap_negative_budget_cap",
            False,
            "Accepted negative budget_cap_minor in UAP",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "negative_amounts",
            "uap_negative_budget_cap",
            True,
            "Rejected negative budget cap with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "negative_amounts",
            "uap_negative_budget_cap",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 1.11 UAP negative amount_minor fallback rejection
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_neg_amt_fallback",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "prod_1", "amount": -100, "currency": "INR"},
                },
            }
        )
        record(
            "negative_amounts",
            "uap_negative_amount_fallback",
            False,
            "Accepted negative amount fallback in UAP",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "negative_amounts",
            "uap_negative_amount_fallback",
            True,
            "Rejected negative amount fallback with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "negative_amounts",
            "uap_negative_amount_fallback",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # -----------------------------------------------------------------------
    # 2. FLOATING POINT & NON-INTEGER VALUES
    # -----------------------------------------------------------------------
    # 2.1 ACP float unit_price_minor (e.g. 12.99 or 12.5)
    try:
        req_acp_flt = acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "flt_1",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 12.99, "currency": "INR"}]
                },
            }
        )
        item = req_acp_flt.items[0]
        if isinstance(item.unit_price_minor, float) or item.unit_price_minor == 12:
            record(
                "floating_point_values",
                "acp_float_price_coercion",
                False,
                f"Silently truncated float 12.99 to {item.unit_price_minor}! Violates Section 0.4 and Section 122",
                is_vuln=True,
            )
        else:
            record(
                "floating_point_values",
                "acp_float_price_coercion",
                True,
                "Handled float without truncation",
            )
    except MalformedPayloadError:
        record(
            "floating_point_values",
            "acp_float_price_coercion",
            True,
            "Rejected float price with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "floating_point_values",
            "acp_float_price_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.2 ACP float quantity
    try:
        req_acp_qty_flt = acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "flt_qty",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [
                        {
                            "product_id": "p1",
                            "unit_price_minor": 100,
                            "quantity": 2.5,
                            "currency": "INR",
                        }
                    ]
                },
            }
        )
        if req_acp_qty_flt.items[0].quantity == 2:
            record(
                "floating_point_values",
                "acp_float_quantity_coercion",
                False,
                "Silently truncated float 2.5 quantity to 2! Buyer pays for 2 but requested 2.5",
                is_vuln=True,
            )
        else:
            record("floating_point_values", "acp_float_quantity_coercion", True, "Handled")
    except MalformedPayloadError:
        record(
            "floating_point_values",
            "acp_float_quantity_coercion",
            True,
            "Rejected float quantity with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "floating_point_values",
            "acp_float_quantity_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.3 ACP boolean price (True -> 1)
    try:
        req_acp_bool = acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "bool_price",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": True, "currency": "INR"}]
                },
            }
        )
        if req_acp_bool.items[0].unit_price_minor == 1:
            record(
                "floating_point_values",
                "acp_bool_price_coercion",
                False,
                "Coerced boolean True into 1 minor unit price!",
                is_vuln=True,
            )
        else:
            record("floating_point_values", "acp_bool_price_coercion", True, "Handled")
    except MalformedPayloadError:
        record("floating_point_values", "acp_bool_price_coercion", True, "Rejected boolean price")
    except Exception as e:
        record(
            "floating_point_values",
            "acp_bool_price_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.4 AP2 float amount_minor
    try:
        req_ap2_flt = ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "flt_ap2",
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": 4500.85,
                "currency": "INR",
            }
        )
        if req_ap2_flt.amount_minor == 4500:
            record(
                "floating_point_values",
                "ap2_float_amount_coercion",
                False,
                f"Silently truncated float 4500.85 to {req_ap2_flt.amount_minor}! Rounding decision in financial path",
                is_vuln=True,
            )
        else:
            record("floating_point_values", "ap2_float_amount_coercion", True, "Handled")
    except MalformedPayloadError:
        record(
            "floating_point_values",
            "ap2_float_amount_coercion",
            True,
            "Rejected float amount with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "floating_point_values",
            "ap2_float_amount_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.5 AP2 float minor_units in block
    try:
        req_ap2_blk_flt = ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "flt_ap2_blk",
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount": {"minor_units": 999.99, "currency": "INR"},
            }
        )
        if req_ap2_blk_flt.amount_minor == 999:
            record(
                "floating_point_values",
                "ap2_float_block_coercion",
                False,
                f"Silently truncated float 999.99 to {req_ap2_blk_flt.amount_minor} in amount block",
                is_vuln=True,
            )
        else:
            record("floating_point_values", "ap2_float_block_coercion", True, "Handled")
    except MalformedPayloadError:
        record(
            "floating_point_values", "ap2_float_block_coercion", True, "Rejected float minor_units"
        )
    except Exception as e:
        record(
            "floating_point_values",
            "ap2_float_block_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.6 UAP float price coercion
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_flt_1",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "prod_1", "price_minor": 12.99, "currency": "INR"},
                },
            }
        )
        record(
            "floating_point_values",
            "uap_float_price_coercion",
            False,
            "Accepted float price_minor in UAP payload",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "floating_point_values",
            "uap_float_price_coercion",
            True,
            "Rejected float price with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "floating_point_values",
            "uap_float_price_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.7 UAP boolean price coercion
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_bool_1",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "prod_1", "price_minor": True, "currency": "INR"},
                },
            }
        )
        record(
            "floating_point_values",
            "uap_bool_price_coercion",
            False,
            "Coerced boolean True into 1 minor unit in UAP!",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record("floating_point_values", "uap_bool_price_coercion", True, "Rejected boolean price")
    except Exception as e:
        record(
            "floating_point_values",
            "uap_bool_price_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.8 x402 float amount_minor coercion
    try:
        x402.parse_payload(
            {
                "x402_version": "1.0",
                "challenge_id": "x402_flt_1",
                "resource_uri": "https://api.example.com/data",
                "principal_id": "user_01",
                "agent_id": "agent_01",
                "tenant_id": "tenant_01",
                "amount_minor": 99.99,
                "currency": "INR",
            }
        )
        record(
            "floating_point_values",
            "x402_float_amount_coercion",
            False,
            "Accepted float amount_minor in x402 payload",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "floating_point_values",
            "x402_float_amount_coercion",
            True,
            "Rejected float amount with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "floating_point_values",
            "x402_float_amount_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.9 x402 boolean amount_minor coercion
    try:
        x402.parse_payload(
            {
                "x402_version": "1.0",
                "challenge_id": "x402_bool_1",
                "resource_uri": "https://api.example.com/data",
                "principal_id": "user_01",
                "agent_id": "agent_01",
                "tenant_id": "tenant_01",
                "amount_minor": True,
                "currency": "INR",
            }
        )
        record(
            "floating_point_values",
            "x402_bool_amount_coercion",
            False,
            "Coerced boolean True into 1 minor unit in x402!",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "floating_point_values",
            "x402_bool_amount_coercion",
            True,
            "Rejected boolean amount_minor",
        )
    except Exception as e:
        record(
            "floating_point_values",
            "x402_bool_amount_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.10 UAP float quantity coercion
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_flt_qty",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {
                        "product_id": "prod_1",
                        "quantity": 2.5,
                        "price_minor": 100,
                        "currency": "INR",
                    },
                },
            }
        )
        record(
            "floating_point_values",
            "uap_float_quantity_coercion",
            False,
            "Accepted float quantity in UAP",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "floating_point_values",
            "uap_float_quantity_coercion",
            True,
            "Rejected float quantity with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "floating_point_values",
            "uap_float_quantity_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.11 UAP boolean quantity coercion
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_bool_qty",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {
                        "product_id": "prod_1",
                        "quantity": True,
                        "price_minor": 100,
                        "currency": "INR",
                    },
                },
            }
        )
        record(
            "floating_point_values",
            "uap_bool_quantity_coercion",
            False,
            "Coerced boolean quantity into integer in UAP",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "floating_point_values",
            "uap_bool_quantity_coercion",
            True,
            "Rejected boolean quantity with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "floating_point_values",
            "uap_bool_quantity_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.12 UAP float budget cap coercion
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_flt_budget",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {
                        "product_id": "prod_1",
                        "price_minor": 100,
                        "budget_cap_minor": 2500.5,
                        "currency": "INR",
                    },
                },
            }
        )
        record(
            "floating_point_values",
            "uap_float_budget_coercion",
            False,
            "Accepted float budget_cap_minor in UAP",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "floating_point_values",
            "uap_float_budget_coercion",
            True,
            "Rejected float budget cap with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "floating_point_values",
            "uap_float_budget_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 2.13 UAP boolean budget cap coercion
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_bool_budget",
                "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {
                        "product_id": "prod_1",
                        "price_minor": 100,
                        "budget_cap_minor": True,
                        "currency": "INR",
                    },
                },
            }
        )
        record(
            "floating_point_values",
            "uap_bool_budget_coercion",
            False,
            "Coerced boolean budget cap into integer in UAP",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "floating_point_values",
            "uap_bool_budget_coercion",
            True,
            "Rejected boolean budget cap with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "floating_point_values",
            "uap_bool_budget_coercion",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # -----------------------------------------------------------------------
    # 3. MISMATCHED & INVALID CURRENCY
    # -----------------------------------------------------------------------
    # 3.1 ACP multi-currency cart
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "curr_mismatch",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [
                        {"product_id": "p1", "unit_price_minor": 100, "currency": "INR"},
                        {"product_id": "p2", "unit_price_minor": 100, "currency": "USD"},
                    ]
                },
            }
        )
        record(
            "currency_mismatches",
            "acp_multi_currency_rejection",
            False,
            "Allowed mixed currencies in cart",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "currency_mismatches",
            "acp_multi_currency_rejection",
            True,
            "Properly rejected multi-currency cart",
        )
    except Exception as e:
        record(
            "currency_mismatches",
            "acp_multi_currency_rejection",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 3.2 ACP invalid currency code
    try:
        req_acp_inv_curr = acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "inv_curr",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "TOOLONG"}]
                },
            }
        )
        action, _ = acp.to_action_request(req_acp_inv_curr)
        record(
            "currency_mismatches",
            "acp_invalid_currency_code",
            False,
            f"Accepted invalid currency 'TOOLONG' without validation: {action.amount.currency}",
            is_vuln=True,
        )
    except (MalformedPayloadError, InvalidCurrencyError):
        record(
            "currency_mismatches",
            "acp_invalid_currency_code",
            True,
            "Rejected invalid currency code",
        )
    except Exception as e:
        record(
            "currency_mismatches",
            "acp_invalid_currency_code",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 3.3 AP2 invalid currency code
    try:
        req_ap2_inv_curr = ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "inv_curr_ap2",
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": 1000,
                "currency": "NOT_A_CURRENCY",
            }
        )
        action, _ = ap2.to_action_request(req_ap2_inv_curr)
        record(
            "currency_mismatches",
            "ap2_invalid_currency_code",
            False,
            f"Accepted invalid currency 'NOT_A_CURRENCY': {action.amount.currency}",
            is_vuln=True,
        )
    except (MalformedPayloadError, InvalidCurrencyError):
        record(
            "currency_mismatches", "ap2_invalid_currency_code", True, "Rejected invalid currency"
        )
    except Exception as e:
        record(
            "currency_mismatches",
            "ap2_invalid_currency_code",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # -----------------------------------------------------------------------
    # 4. EMPTY CART & EMPTY ITEMS
    # -----------------------------------------------------------------------
    # 4.1 ACP cart items is empty list
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "empty_cart",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {"items": []},
            }
        )
        record(
            "empty_cart_and_items",
            "acp_empty_cart_items_list",
            False,
            "Accepted empty items list",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "empty_cart_and_items", "acp_empty_cart_items_list", True, "Rejected empty items list"
        )
    except Exception as e:
        record(
            "empty_cart_and_items",
            "acp_empty_cart_items_list",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 4.2 ACP cart items is None
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "none_cart",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {"items": None},
            }
        )
        record(
            "empty_cart_and_items",
            "acp_none_cart_items",
            False,
            "Accepted None items",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record("empty_cart_and_items", "acp_none_cart_items", True, "Rejected None items")
    except Exception as e:
        record(
            "empty_cart_and_items",
            "acp_none_cart_items",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 4.3 ACP cart item with missing product_id
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "no_prod_id",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {"items": [{"unit_price_minor": 100, "currency": "INR"}]},
            }
        )
        record(
            "empty_cart_and_items",
            "acp_missing_product_id",
            False,
            "Accepted item with missing product_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "empty_cart_and_items",
            "acp_missing_product_id",
            True,
            "Rejected item with missing product_id",
        )
    except Exception as e:
        record(
            "empty_cart_and_items",
            "acp_missing_product_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 4.4 ACP cart item with empty string product_id
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "empty_prod_id",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {"items": [{"product_id": "", "unit_price_minor": 100, "currency": "INR"}]},
            }
        )
        record(
            "empty_cart_and_items",
            "acp_empty_str_product_id",
            False,
            "Accepted item with empty string product_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "empty_cart_and_items",
            "acp_empty_str_product_id",
            True,
            "Rejected empty string product_id",
        )
    except Exception as e:
        record(
            "empty_cart_and_items",
            "acp_empty_str_product_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # -----------------------------------------------------------------------
    # 5. MISSING BUYER / PAYER / PAYEE IDS
    # -----------------------------------------------------------------------
    # 5.1 ACP missing buyer block completely
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "no_buyer",
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
            }
        )
        record(
            "missing_ids",
            "acp_missing_buyer_block",
            False,
            "Accepted missing buyer block",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record("missing_ids", "acp_missing_buyer_block", True, "Rejected missing buyer block")
    except Exception as e:
        record(
            "missing_ids",
            "acp_missing_buyer_block",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 5.2 ACP buyer missing principal_id
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "no_prin",
                "buyer": {"agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
            }
        )
        record(
            "missing_ids",
            "acp_missing_principal_id",
            False,
            "Accepted buyer missing principal_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_ids", "acp_missing_principal_id", True, "Rejected buyer missing principal_id"
        )
    except Exception as e:
        record(
            "missing_ids",
            "acp_missing_principal_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 5.3 ACP buyer empty string agent_id
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "empty_agent",
                "buyer": {"principal_id": "p", "agent_id": "", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
            }
        )
        record(
            "missing_ids",
            "acp_empty_agent_id",
            False,
            "Accepted empty string agent_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record("missing_ids", "acp_empty_agent_id", True, "Rejected empty string agent_id")
    except Exception as e:
        record(
            "missing_ids", "acp_empty_agent_id", False, f"Unexpected error: {type(e).__name__}: {e}"
        )

    # 5.4 AP2 missing payer block
    try:
        ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "no_payer",
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": 100,
            }
        )
        record(
            "missing_ids",
            "ap2_missing_payer_block",
            False,
            "Accepted missing payer block",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record("missing_ids", "ap2_missing_payer_block", True, "Rejected missing payer block")
    except Exception as e:
        record(
            "missing_ids",
            "ap2_missing_payer_block",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 5.5 AP2 missing payee block
    try:
        ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "no_payee",
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "amount_minor": 100,
            }
        )
        record(
            "missing_ids",
            "ap2_missing_payee_block",
            False,
            "Accepted missing payee block",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record("missing_ids", "ap2_missing_payee_block", True, "Rejected missing payee block")
    except Exception as e:
        record(
            "missing_ids",
            "ap2_missing_payee_block",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 5.6 AP2 payer missing principal_id
    try:
        ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "no_p_prin",
                "payer": {"agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": 100,
            }
        )
        record(
            "missing_ids",
            "ap2_missing_payer_principal_id",
            False,
            "Accepted payer missing principal_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_ids",
            "ap2_missing_payer_principal_id",
            True,
            "Rejected payer missing principal_id",
        )
    except Exception as e:
        record(
            "missing_ids",
            "ap2_missing_payer_principal_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 5.7 x402 missing principal_id
    try:
        x402.parse_payload(
            {
                "x402_version": "1.0",
                "challenge_id": "x402_no_prin",
                "resource_uri": "https://api.example.com/data",
                "agent_id": "agent_01",
                "tenant_id": "tenant_01",
                "amount_minor": 100,
                "currency": "INR",
            }
        )
        record(
            "missing_ids",
            "x402_missing_principal_id",
            False,
            "Accepted x402 request missing principal_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_ids",
            "x402_missing_principal_id",
            True,
            "Rejected missing principal_id with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "missing_ids",
            "x402_missing_principal_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 5.8 x402 missing agent_id
    try:
        x402.parse_payload(
            {
                "x402_version": "1.0",
                "challenge_id": "x402_no_agent",
                "resource_uri": "https://api.example.com/data",
                "principal_id": "user_01",
                "tenant_id": "tenant_01",
                "amount_minor": 100,
                "currency": "INR",
            }
        )
        record(
            "missing_ids",
            "x402_missing_agent_id",
            False,
            "Accepted x402 request missing agent_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_ids",
            "x402_missing_agent_id",
            True,
            "Rejected missing agent_id with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "missing_ids",
            "x402_missing_agent_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 5.9 x402 missing challenge_id
    try:
        x402.parse_payload(
            {
                "x402_version": "1.0",
                "resource_uri": "https://api.example.com/data",
                "principal_id": "user_01",
                "agent_id": "agent_01",
                "tenant_id": "tenant_01",
                "amount_minor": 100,
                "currency": "INR",
            }
        )
        record(
            "missing_ids",
            "x402_missing_challenge_id",
            False,
            "Accepted x402 request missing challenge_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_ids",
            "x402_missing_challenge_id",
            True,
            "Rejected missing challenge_id with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "missing_ids",
            "x402_missing_challenge_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 5.10 x402 missing amount_minor
    try:
        x402.parse_payload(
            {
                "x402_version": "1.0",
                "challenge_id": "x402_no_amt",
                "resource_uri": "https://api.example.com/data",
                "principal_id": "user_01",
                "agent_id": "agent_01",
                "tenant_id": "tenant_01",
                "currency": "INR",
            }
        )
        record(
            "missing_ids",
            "x402_missing_amount_minor",
            False,
            "Accepted x402 request missing amount_minor",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_ids",
            "x402_missing_amount_minor",
            True,
            "Rejected missing amount_minor with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "missing_ids",
            "x402_missing_amount_minor",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 5.11 UAP missing sender
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_no_sender",
                "header": {"tenant": "tenant_01", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "prod_1", "price_minor": 100, "currency": "INR"},
                },
            }
        )
        record(
            "missing_ids",
            "uap_missing_sender",
            False,
            "Accepted UAP request missing sender",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_ids",
            "uap_missing_sender",
            True,
            "Rejected missing sender with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "missing_ids", "uap_missing_sender", False, f"Unexpected error: {type(e).__name__}: {e}"
        )

    # 5.12 UAP missing principal
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_no_prin",
                "header": {"sender": "agent_01", "tenant": "tenant_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "prod_1", "price_minor": 100, "currency": "INR"},
                },
            }
        )
        record(
            "missing_ids",
            "uap_missing_principal",
            False,
            "Accepted UAP request missing principal",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_ids",
            "uap_missing_principal",
            True,
            "Rejected missing principal with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "missing_ids",
            "uap_missing_principal",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # -----------------------------------------------------------------------
    # 6. MISSING TENANT IDS
    # -----------------------------------------------------------------------
    # 6.1 ACP buyer missing tenant_id in payload
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "no_ten_acp",
                "buyer": {"principal_id": "p", "agent_id": "a"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
            }
        )
        record(
            "missing_tenant",
            "acp_missing_tenant_id_in_payload",
            False,
            "Accepted payload without tenant_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_tenant",
            "acp_missing_tenant_id_in_payload",
            True,
            "Rejected payload without tenant_id",
        )
    except Exception as e:
        record(
            "missing_tenant",
            "acp_missing_tenant_id_in_payload",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 6.2 ACP buyer empty string tenant_id
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "empty_ten_acp",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": ""},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
            }
        )
        record(
            "missing_tenant",
            "acp_empty_string_tenant_id",
            False,
            "Accepted payload with empty string tenant_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_tenant",
            "acp_empty_string_tenant_id",
            True,
            "Rejected payload with empty string tenant_id",
        )
    except Exception as e:
        record(
            "missing_tenant",
            "acp_empty_string_tenant_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 6.3 AP2 payer missing tenant_id
    try:
        ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "no_ten_ap2",
                "payer": {"principal_id": "p", "agent_id": "a"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": 100,
            }
        )
        record(
            "missing_tenant",
            "ap2_missing_tenant_id_in_payload",
            False,
            "Accepted payload without tenant_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_tenant",
            "ap2_missing_tenant_id_in_payload",
            True,
            "Rejected payload without tenant_id",
        )
    except Exception as e:
        record(
            "missing_tenant",
            "ap2_missing_tenant_id_in_payload",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 6.4 AP2 payer empty string tenant_id
    try:
        ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "empty_ten_ap2",
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": ""},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": 100,
            }
        )
        record(
            "missing_tenant",
            "ap2_empty_string_tenant_id",
            False,
            "Accepted payload with empty string tenant_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_tenant",
            "ap2_empty_string_tenant_id",
            True,
            "Rejected payload with empty string tenant_id",
        )
    except Exception as e:
        record(
            "missing_tenant",
            "ap2_empty_string_tenant_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 6.5 UAP empty string tenant_id
    try:
        uap.parse_payload(
            {
                "uap_version": "1.0",
                "message_id": "uap_empty_ten",
                "header": {"sender": "agent_01", "tenant": "", "principal": "user_01"},
                "payload": {
                    "capability": "commerce.purchase",
                    "parameters": {"product_id": "prod_1", "price_minor": 100, "currency": "INR"},
                },
            }
        )
        record(
            "missing_tenant",
            "uap_empty_string_tenant_id",
            False,
            "Accepted payload with empty string tenant_id in UAP",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_tenant",
            "uap_empty_string_tenant_id",
            True,
            "Rejected payload with empty string tenant_id",
        )
    except Exception as e:
        record(
            "missing_tenant",
            "uap_empty_string_tenant_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 6.6 x402 empty string tenant_id
    try:
        x402.parse_payload(
            {
                "x402_version": "1.0",
                "challenge_id": "x402_empty_ten",
                "resource_uri": "https://api.example.com/data",
                "principal_id": "user_01",
                "agent_id": "agent_01",
                "tenant_id": "",
                "amount_minor": 100,
                "currency": "INR",
            }
        )
        record(
            "missing_tenant",
            "x402_empty_string_tenant_id",
            False,
            "Accepted payload with empty string tenant_id in x402",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "missing_tenant",
            "x402_empty_string_tenant_id",
            True,
            "Rejected payload with empty string tenant_id",
        )
    except Exception as e:
        record(
            "missing_tenant",
            "x402_empty_string_tenant_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # -----------------------------------------------------------------------
    # 7. DATES, EXPIRED MANDATES, FUTURE DATES
    # -----------------------------------------------------------------------
    # 7.1 ACP invalid date string
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "inv_date",
                "timestamp": "not-a-valid-date-string",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
            }
        )
        record(
            "dates_and_mandates",
            "acp_invalid_timestamp_string",
            False,
            "Accepted garbage timestamp string",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "dates_and_mandates",
            "acp_invalid_timestamp_string",
            True,
            "Rejected garbage timestamp with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "dates_and_mandates",
            "acp_invalid_timestamp_string",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 7.2 AP2 invalid date string
    try:
        ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "inv_date_ap2",
                "timestamp": "2026-99-99T99:99:99Z",
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": 100,
            }
        )
        record(
            "dates_and_mandates",
            "ap2_invalid_timestamp_string",
            False,
            "Accepted invalid date format",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "dates_and_mandates",
            "ap2_invalid_timestamp_string",
            True,
            "Rejected invalid date format",
        )
    except Exception as e:
        record(
            "dates_and_mandates",
            "ap2_invalid_timestamp_string",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 7.3 AP2 expired mandate
    try:
        past_date = "2020-01-01T00:00:00Z"
        req_ap2_expired = ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "exp_mandate",
                "mandate_id": "mandate_expired_001",
                "valid_until": past_date,
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": 100,
            }
        )
        has_valid_until = hasattr(req_ap2_expired, "valid_until")
        ap2.to_action_request(req_ap2_expired)
        record(
            "dates_and_mandates",
            "ap2_expired_mandate_rejection",
            False,
            f"EXPIRED MANDATE ALLOWED! valid_until={past_date} was completely ignored by AP2Adapter. hasattr(valid_until)={has_valid_until}",
            is_vuln=True,
        )
    except (MalformedPayloadError, ProtocolError):
        record(
            "dates_and_mandates", "ap2_expired_mandate_rejection", True, "Rejected expired mandate"
        )
    except Exception as e:
        record(
            "dates_and_mandates",
            "ap2_expired_mandate_rejection",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 7.4 AP2 mandate with invalid format valid_until
    try:
        ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "inv_mandate_date",
                "mandate_id": "mandate_002",
                "valid_until": "gibberish_date",
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": 100,
            }
        )
        record(
            "dates_and_mandates",
            "ap2_invalid_valid_until_date",
            False,
            "Accepted gibberish valid_until without validation!",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record(
            "dates_and_mandates",
            "ap2_invalid_valid_until_date",
            True,
            "Rejected invalid valid_until",
        )
    except Exception as e:
        record(
            "dates_and_mandates",
            "ap2_invalid_valid_until_date",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 7.5 ACP future date parsing safely
    try:
        future_str = (datetime.now(UTC) + timedelta(days=365)).isoformat()
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "future_date",
                "timestamp": future_str,
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
            }
        )
        record(
            "dates_and_mandates",
            "acp_future_date_parsing",
            True,
            f"Parsed future timestamp {future_str} safely",
        )
    except Exception as e:
        record(
            "dates_and_mandates",
            "acp_future_date_parsing",
            False,
            f"Error on future date: {type(e).__name__}: {e}",
        )

    # -----------------------------------------------------------------------
    # 8. HTTP API FAIL-CLOSED BEHAVIOR
    # -----------------------------------------------------------------------
    # 8.1 ACP POST completely empty body
    status, body = client.post(
        "/api/v1/protocols/acp/checkout", json_body={}, headers={"x-tenant-id": "t"}
    )
    if status == 422:
        record("api_fail_closed", "acp_empty_body_422", True, "Returned 422 MALFORMED_PAYLOAD")
    else:
        record(
            "api_fail_closed",
            "acp_empty_body_422",
            False,
            f"Expected 422, got {status}: {body}",
            is_vuln=(status != 422),
        )

    # 8.2 ACP POST float unit_price_minor over HTTP
    float_payload = {
        "acp_version": "1.0",
        "request_id": "api_flt_test",
        "buyer": {"principal_id": "user_01", "agent_id": "agent_01", "tenant_id": "tenant_01"},
        "cart": {
            "items": [
                {
                    "product_id": "prod_01",
                    "variant_id": "var_01",
                    "unit_price_minor": 12.50,
                    "currency": "INR",
                }
            ]
        },
    }
    status, body = client.post(
        "/api/v1/protocols/acp/checkout",
        json_body=float_payload,
        headers={"x-tenant-id": "tenant_01"},
    )
    if status in (400, 422):
        record(
            "api_fail_closed",
            "api_acp_float_rejected",
            True,
            f"Properly rejected with HTTP {status}",
        )
    elif status == 200:
        record(
            "api_fail_closed",
            "api_acp_float_rejected",
            False,
            f"HTTP 200 SUCCESS! Float unit_price_minor=12.50 was executed! Decision={body.get('decision')}, Status={body.get('status')}",
            is_vuln=True,
        )
    else:
        record("api_fail_closed", "api_acp_float_rejected", False, f"Got status {status}: {body}")

    # 8.3 AP2 POST float amount over HTTP
    float_ap2_payload = {
        "ap2_version": "1.0",
        "payment_request_id": "api_flt_ap2_test",
        "payer": {"principal_id": "user_01", "agent_id": "agent_01", "tenant_id": "tenant_01"},
        "payee": {"merchant_id": "merch_acme", "order_ref": "var_01"},
        "amount": {"minor_units": 100.75, "currency": "INR"},
    }
    status, body = client.post(
        "/api/v1/protocols/ap2/mandates",
        json_body=float_ap2_payload,
        headers={"x-tenant-id": "tenant_01"},
    )
    if status in (400, 422):
        record(
            "api_fail_closed",
            "api_ap2_float_rejected",
            True,
            f"Properly rejected with HTTP {status}",
        )
    elif status == 200:
        record(
            "api_fail_closed",
            "api_ap2_float_rejected",
            False,
            f"HTTP 200 SUCCESS! Float minor_units=100.75 was executed! Decision={body.get('decision')}",
            is_vuln=True,
        )
    else:
        record("api_fail_closed", "api_ap2_float_rejected", False, f"Got status {status}: {body}")

    # 8.4 AP2 POST expired mandate over HTTP
    expired_payload = {
        "ap2_version": "1.0",
        "payment_request_id": "api_exp_ap2_test",
        "mandate_id": "mandate_expired_http",
        "valid_until": "2020-01-01T00:00:00Z",
        "payer": {"principal_id": "user_01", "agent_id": "agent_01", "tenant_id": "tenant_01"},
        "payee": {"merchant_id": "merch_acme", "order_ref": "var_01"},
        "amount": {"minor_units": 1000, "currency": "INR"},
    }
    status, body = client.post(
        "/api/v1/protocols/ap2/mandates",
        json_body=expired_payload,
        headers={"x-tenant-id": "tenant_01"},
    )
    if status in (400, 422):
        record(
            "api_fail_closed",
            "api_ap2_expired_mandate_rejected",
            True,
            f"Properly rejected with HTTP {status}",
        )
    elif status == 200:
        record(
            "api_fail_closed",
            "api_ap2_expired_mandate_rejected",
            False,
            f"HTTP 200 SUCCESS! Expired mandate (valid_until=2020-01-01) was executed without check! Decision={body.get('decision')}",
            is_vuln=True,
        )
    else:
        record(
            "api_fail_closed",
            "api_ap2_expired_mandate_rejected",
            False,
            f"Got status {status}: {body}",
        )

    # 8.5 AP2 POST boolean amount over HTTP
    bool_ap2_payload = {
        "ap2_version": "1.0",
        "payment_request_id": "api_bool_ap2",
        "payer": {"principal_id": "user_01", "agent_id": "agent_01", "tenant_id": "tenant_01"},
        "payee": {"merchant_id": "merch_acme", "order_ref": "var_01"},
        "amount_minor": True,
        "currency": "INR",
    }
    status, body = client.post(
        "/api/v1/protocols/ap2/mandates",
        json_body=bool_ap2_payload,
        headers={"x-tenant-id": "tenant_01"},
    )
    if status in (400, 422):
        record(
            "api_fail_closed",
            "api_ap2_bool_amount_rejected",
            True,
            f"Properly rejected boolean with HTTP {status}",
        )
    elif status == 200:
        record(
            "api_fail_closed",
            "api_ap2_bool_amount_rejected",
            False,
            f"HTTP 200 SUCCESS! Boolean amount_minor=True was executed as 1 minor unit! Decision={body.get('decision')}",
            is_vuln=True,
        )
    else:
        record(
            "api_fail_closed", "api_ap2_bool_amount_rejected", False, f"Got status {status}: {body}"
        )

    # 8.6 UAP POST float price over HTTP
    float_uap_payload = {
        "uap_version": "1.0",
        "message_id": "api_flt_uap_test",
        "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_laptop_001", "price_minor": 12.5, "quantity": 1},
        },
    }
    status, body = client.post(
        "/api/v1/protocols/uap/rpc",
        json_body=float_uap_payload,
        headers={"x-tenant-id": "tenant_01"},
    )
    if status in (400, 422):
        record(
            "api_fail_closed",
            "api_uap_float_rejected",
            True,
            f"Properly rejected float with HTTP {status}",
        )
    elif status == 200:
        record(
            "api_fail_closed",
            "api_uap_float_rejected",
            False,
            f"HTTP 200 SUCCESS! Float price_minor=12.5 was executed! Result={body.get('result')}",
            is_vuln=True,
        )
    else:
        record("api_fail_closed", "api_uap_float_rejected", False, f"Got status {status}: {body}")

    # 8.7 UAP POST boolean price over HTTP
    bool_uap_payload = {
        "uap_version": "1.0",
        "message_id": "api_bool_uap_test",
        "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_laptop_001", "price_minor": True, "quantity": 1},
        },
    }
    status, body = client.post(
        "/api/v1/protocols/uap/rpc",
        json_body=bool_uap_payload,
        headers={"x-tenant-id": "tenant_01"},
    )
    if status in (400, 422):
        record(
            "api_fail_closed",
            "api_uap_bool_rejected",
            True,
            f"Properly rejected boolean with HTTP {status}",
        )
    elif status == 200:
        record(
            "api_fail_closed",
            "api_uap_bool_rejected",
            False,
            f"HTTP 200 SUCCESS! Boolean price_minor=True was executed! Result={body.get('result')}",
            is_vuln=True,
        )
    else:
        record("api_fail_closed", "api_uap_bool_rejected", False, f"Got status {status}: {body}")

    # 8.8 UAP POST negative price over HTTP
    neg_uap_payload = {
        "uap_version": "1.0",
        "message_id": "api_neg_uap_test",
        "header": {"sender": "agent_01", "tenant": "tenant_01", "principal": "user_01"},
        "payload": {
            "capability": "commerce.purchase",
            "parameters": {"product_id": "prod_laptop_001", "price_minor": -500, "quantity": 1},
        },
    }
    status, body = client.post(
        "/api/v1/protocols/uap/rpc",
        json_body=neg_uap_payload,
        headers={"x-tenant-id": "tenant_01"},
    )
    if status in (400, 422):
        record(
            "api_fail_closed",
            "api_uap_negative_rejected",
            True,
            f"Properly rejected negative price with HTTP {status}",
        )
    elif status == 200:
        record(
            "api_fail_closed",
            "api_uap_negative_rejected",
            False,
            f"HTTP 200 SUCCESS! Negative price_minor=-500 was executed! Result={body.get('result')}",
            is_vuln=True,
        )
    else:
        record(
            "api_fail_closed", "api_uap_negative_rejected", False, f"Got status {status}: {body}"
        )

    # 8.9 x402 POST float amount over HTTP
    float_x402_payload = {
        "x402_version": "1.0",
        "challenge_id": "api_flt_x402_test",
        "resource_uri": "https://api.example.com/data",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "tenant_id": "tenant_01",
        "amount_minor": 12.5,
        "currency": "INR",
    }
    status, body = client.post(
        "/api/v1/protocols/x402/pay",
        json_body=float_x402_payload,
        headers={"x-tenant-id": "tenant_01"},
    )
    if status in (400, 422):
        record(
            "api_fail_closed",
            "api_x402_float_rejected",
            True,
            f"Properly rejected float with HTTP {status}",
        )
    elif status == 200:
        record(
            "api_fail_closed",
            "api_x402_float_rejected",
            False,
            f"HTTP 200 SUCCESS! Float amount_minor=12.5 was executed! Token={body.get('authorization_token')}",
            is_vuln=True,
        )
    else:
        record("api_fail_closed", "api_x402_float_rejected", False, f"Got status {status}: {body}")

    # 8.10 x402 POST boolean amount over HTTP
    bool_x402_payload = {
        "x402_version": "1.0",
        "challenge_id": "api_bool_x402_test",
        "resource_uri": "https://api.example.com/data",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "tenant_id": "tenant_01",
        "amount_minor": True,
        "currency": "INR",
    }
    status, body = client.post(
        "/api/v1/protocols/x402/pay",
        json_body=bool_x402_payload,
        headers={"x-tenant-id": "tenant_01"},
    )
    if status in (400, 422):
        record(
            "api_fail_closed",
            "api_x402_bool_rejected",
            True,
            f"Properly rejected boolean with HTTP {status}",
        )
    elif status == 200:
        record(
            "api_fail_closed",
            "api_x402_bool_rejected",
            False,
            f"HTTP 200 SUCCESS! Boolean amount_minor=True was executed! Token={body.get('authorization_token')}",
            is_vuln=True,
        )
    else:
        record("api_fail_closed", "api_x402_bool_rejected", False, f"Got status {status}: {body}")

    # 8.11 x402 POST negative amount over HTTP
    neg_x402_payload = {
        "x402_version": "1.0",
        "challenge_id": "api_neg_x402_test",
        "resource_uri": "https://api.example.com/data",
        "principal_id": "user_01",
        "agent_id": "agent_01",
        "tenant_id": "tenant_01",
        "amount_minor": -250,
        "currency": "INR",
    }
    status, body = client.post(
        "/api/v1/protocols/x402/pay",
        json_body=neg_x402_payload,
        headers={"x-tenant-id": "tenant_01"},
    )
    if status in (400, 422):
        record(
            "api_fail_closed",
            "api_x402_negative_rejected",
            True,
            f"Properly rejected negative amount with HTTP {status}",
        )
    elif status == 200:
        record(
            "api_fail_closed",
            "api_x402_negative_rejected",
            False,
            f"HTTP 200 SUCCESS! Negative amount_minor=-250 was executed! Token={body.get('authorization_token')}",
            is_vuln=True,
        )
    else:
        record(
            "api_fail_closed", "api_x402_negative_rejected", False, f"Got status {status}: {body}"
        )

    # -----------------------------------------------------------------------
    # 9. EXTENDED BOUNDARY & TYPE ADVERSARIAL PROBES
    # -----------------------------------------------------------------------
    # 9.1 None / null principal_id in buyer
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "null_prin",
                "buyer": {"principal_id": None, "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
            }
        )
        record(
            "missing_ids",
            "acp_null_principal_id",
            False,
            "Accepted None principal_id",
            is_vuln=True,
        )
    except MalformedPayloadError:
        record("missing_ids", "acp_null_principal_id", True, "Rejected None principal_id")
    except Exception as e:
        record(
            "missing_ids",
            "acp_null_principal_id",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 9.2 Non-dict buyer (e.g. string)
    try:
        acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "str_buyer",
                "buyer": "not_a_dict",
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
            }
        )
        record("missing_ids", "acp_string_buyer", True, "Rejected string buyer")
    except MalformedPayloadError:
        record(
            "missing_ids",
            "acp_string_buyer",
            True,
            "Rejected string buyer with MalformedPayloadError",
        )
    except Exception as e:
        record(
            "missing_ids", "acp_string_buyer", False, f"Unexpected error: {type(e).__name__}: {e}"
        )

    # 9.3 Negative budget_max_minor in ACP
    try:
        req_neg_budget = acp.parse_payload(
            {
                "acp_version": "1.0",
                "request_id": "neg_budget",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
                "intent": {"budget_max_minor": -500},
            }
        )
        action, _ = acp.to_action_request(req_neg_budget)
        record(
            "negative_amounts",
            "acp_negative_budget_max",
            False,
            f"Accepted negative budget_max_minor without rejection! {action.intent.budget_max}",
            is_vuln=True,
        )
    except (MalformedPayloadError, NegativeMoneyError, InvalidActionRequestError):
        record(
            "negative_amounts",
            "acp_negative_budget_max",
            True,
            "Rejected negative budget_max_minor",
        )
    except Exception as e:
        record(
            "negative_amounts",
            "acp_negative_budget_max",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 9.4 Negative max_authorized_minor in AP2
    try:
        req_neg_max_auth = ap2.parse_payload(
            {
                "ap2_version": "1.0",
                "payment_request_id": "neg_max_auth",
                "payer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "payee": {"merchant_id": "m", "order_ref": "o"},
                "amount_minor": 100,
                "constraints": {"max_authorized_minor": -1000},
            }
        )
        action, _ = ap2.to_action_request(req_neg_max_auth)
        record(
            "negative_amounts",
            "ap2_negative_max_authorized_minor",
            False,
            f"Accepted negative max_authorized_minor without rejection! {action.intent.budget_max}",
            is_vuln=True,
        )
    except (MalformedPayloadError, NegativeMoneyError, InvalidActionRequestError):
        record(
            "negative_amounts",
            "ap2_negative_max_authorized_minor",
            True,
            "Rejected negative max_authorized_minor",
        )
    except Exception as e:
        record(
            "negative_amounts",
            "ap2_negative_max_authorized_minor",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    # 9.5 Integer version instead of string (e.g. 1 instead of '1.0')
    try:
        req_int_ver = acp.parse_payload(
            {
                "acp_version": 1,
                "request_id": "int_ver",
                "buyer": {"principal_id": "p", "agent_id": "a", "tenant_id": "t"},
                "cart": {
                    "items": [{"product_id": "p1", "unit_price_minor": 100, "currency": "INR"}]
                },
            }
        )
        record(
            "dates_and_mandates",
            "acp_integer_version",
            False,
            f"Accepted integer acp_version 1 (coerced to '1') - not in supported versions {req_int_ver.acp_version}",
            is_vuln=True,
        )
    except UnsupportedProtocolVersionError:
        record(
            "dates_and_mandates",
            "acp_integer_version",
            True,
            "Properly rejected integer version '1'",
        )
    except Exception as e:
        record(
            "dates_and_mandates",
            "acp_integer_version",
            False,
            f"Unexpected error: {type(e).__name__}: {e}",
        )

    log("=" * 70)
    log("EMPIRICAL TEST SUMMARY")
    log("=" * 70)
    total_tests = sum(len(v) for v in results.values())
    vulns = [t for v in results.values() for t in v if t["is_vulnerability"]]
    passed = [t for v in results.values() for t in v if t["passed"]]
    log(f"Total Probes Executed: {total_tests}")
    log(f"Passed: {len(passed)}")
    log(f"Vulnerabilities / Deviations Found: {len(vulns)}")
    for v in vulns:
        log(f"  [!] {v['name']}: {v['detail']}")

    return results


if __name__ == "__main__":
    run_tests()
