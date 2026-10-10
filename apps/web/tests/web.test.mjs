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

test("Frontend ARIA and keyboard accessibility contracts per §121", async () => {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const url = await import("node:url");

  const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
  const srcDir = path.resolve(__dirname, "../src");

  // 1. Modal dialog ARIA & Escape key handling
  const modalContent = fs.readFileSync(path.join(srcDir, "components/ui/Modal.tsx"), "utf-8");
  assert.ok(modalContent.includes('role={role}'), "Modal must specify dialog role");
  assert.ok(modalContent.includes('aria-modal="true"'), "Modal must set aria-modal='true'");
  assert.ok(modalContent.includes('aria-labelledby="modal-title"'), "Modal must set aria-labelledby");
  assert.ok(modalContent.includes('key === "Escape"'), "Modal must handle Escape key for keyboard dismissal");
  assert.ok(modalContent.includes('aria-label="Close dialog"'), "Close button must have accessible label");

  // 2. Emergency Banner Alert ARIA
  const bannerContent = fs.readFileSync(path.join(srcDir, "features/emergency/EmergencyBanner.tsx"), "utf-8");
  assert.ok(bannerContent.includes('role="alert"'), "EmergencyBanner must have role='alert'");
  assert.ok(bannerContent.includes('aria-live="assertive"'), "EmergencyBanner must have aria-live='assertive'");
  assert.ok(bannerContent.includes('aria-atomic="true"'), "EmergencyBanner must have aria-atomic='true'");

  // 3. Kill Switch button state and dialog popup ARIA
  const killBtnContent = fs.readFileSync(path.join(srcDir, "features/emergency/KillSwitchButton.tsx"), "utf-8");
  assert.ok(killBtnContent.includes('aria-pressed='), "KillSwitchButton must indicate pressed state");
  assert.ok(killBtnContent.includes('aria-haspopup="dialog"'), "KillSwitchButton must declare popup dialog");
  assert.ok(killBtnContent.includes('aria-label='), "KillSwitchButton must provide accessible label");

  // 4. Navigation Tablist and Tab ARIA
  const navContent = fs.readFileSync(path.join(srcDir, "components/layout/Navbar.tsx"), "utf-8");
  assert.ok(navContent.includes('role="tablist"'), "Navbar must provide role='tablist'");
  assert.ok(navContent.includes('role="tab"'), "Navbar items must have role='tab'");
  assert.ok(navContent.includes('aria-selected='), "Navbar tabs must track aria-selected");
  assert.ok(navContent.includes('aria-controls='), "Navbar tabs must bind aria-controls");

  // 5. Main Content Tabpanel ARIA
  const appContent = fs.readFileSync(path.join(srcDir, "app/App.tsx"), "utf-8");
  assert.ok(appContent.includes('role="tabpanel"'), "App active area must have role='tabpanel'");
  assert.ok(appContent.includes('aria-labelledby='), "App active area must bind aria-labelledby");

  // 6. Approval Queue Region and List ARIA
  const approvalListContent = fs.readFileSync(path.join(srcDir, "features/approval/ApprovalList.tsx"), "utf-8");
  assert.ok(approvalListContent.includes('role="region"'), "ApprovalList must define landmark region");
  assert.ok(approvalListContent.includes('role="list"'), "ApprovalList must declare role='list'");
  assert.ok(approvalListContent.includes('role="listitem"'), "Approval items must declare role='listitem'");
});

