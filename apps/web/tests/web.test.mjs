import test from "node:test";
import assert from "node:assert/strict";
import { DEMO_SCENARIOS } from "../src/services/scenarioData.ts";
import { MOCK_APPROVAL_ITEMS } from "../src/services/mockData.ts";

test("Demo Scenarios 1–8 completeness per §112", () => {
  assert.equal(DEMO_SCENARIOS.length, 8, "Must contain all 8 scenarios");

  const expectedOutcomes = [
    "ALLOW",
    "REVIEW",
    "DENY",
    "PRICE_MISMATCH",
    "INVENTORY_MISMATCH",
    "RECONCILIATION",
    "EVIDENCE_CONFLICT",
    "DENY",
  ];

  DEMO_SCENARIOS.forEach((scen, idx) => {
    assert.equal(scen.number, idx + 1, `Scenario number must match index + 1`);
    assert.equal(
      scen.expectedOutcome,
      expectedOutcomes[idx],
      `Scenario ${idx + 1} outcome mismatch`
    );

    // Each scenario must have all 10 inspectable pipeline steps (§111)
    assert.equal(scen.steps.length, 10, `Scenario ${idx + 1} must contain all 10 steps`);

    const stepIds = scen.steps.map((s) => s.id);
    const expectedStepIds = [
      "USER_INTENT",
      "AGENT_IDENTITY",
      "PRODUCT_DISCOVERY",
      "EVIDENCE",
      "RECOMMENDATION",
      "JEV",
      "POLICY",
      "APPROVAL",
      "PAYMENT",
      "ORDER",
    ];
    assert.deepEqual(stepIds, expectedStepIds, `Scenario ${idx + 1} step sequence mismatch`);
  });
});

test("Human Approval Screen displays all 11 decision context fields per §66", () => {
  assert.ok(MOCK_APPROVAL_ITEMS.length > 0, "Must contain approval items");

  for (const item of MOCK_APPROVAL_ITEMS) {
    // 1. WHO
    assert.ok(item.who && item.who.agentId && item.who.principalId, "Missing WHO field");
    // 2. WHAT
    assert.ok(item.what && item.what.actionType && item.what.domain, "Missing WHAT field");
    // 3. WHY
    assert.ok(item.why && item.why.intentPurpose, "Missing WHY field");
    // 4. HOW MUCH
    assert.ok(item.howMuch && typeof item.howMuch.amountMinor === "number", "Missing HOW MUCH field");
    // 5. MERCHANT
    assert.ok(item.merchant && item.merchant.name, "Missing MERCHANT field");
    // 6. PRODUCT
    assert.ok(item.product && item.product.productId, "Missing PRODUCT field");
    // 7. EVIDENCE
    assert.ok(item.evidence && item.evidence.priceSource && item.evidence.hashSha256, "Missing EVIDENCE field");
    // 8. POLICY
    assert.ok(item.policy && item.policy.triggeredRule, "Missing POLICY field");
    // 9. RISK
    assert.ok(item.risk && item.risk.level && typeof item.risk.score === "number", "Missing RISK field");
    // 10. RECOMMENDATION
    assert.ok(item.recommendation && item.recommendation.advisor && item.recommendation.action, "Missing RECOMMENDATION field");
    // 11. CONFLICTS
    assert.ok(Array.isArray(item.conflicts), "Missing CONFLICTS field");
  }
});

test("Emergency Kill Switch state transitions per §21 and §67", async () => {
  const { apiService } = await import("../src/services/api.ts");

  // Initial state should not be paused
  let state = await apiService.getEmergencyState();
  assert.equal(state.isPaused, false);

  // Trigger emergency pause
  state = await apiService.triggerEmergencyPause("sec_operator_42", "Threat detection simulation");
  assert.equal(state.isPaused, true);
  assert.equal(state.pausedBy, "sec_operator_42");

  // Resume operations
  state = await apiService.triggerEmergencyResume("sec_admin");
  assert.equal(state.isPaused, false);
});
