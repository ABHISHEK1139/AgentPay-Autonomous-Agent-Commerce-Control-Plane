export interface ApprovalItem {
  id: string;
  tenantId: string;
  actionId: string;
  actionVersion: number;
  status: "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" | "CANCELLED";
  createdAt: string;
  expiresAt: string;

  // The 11 Mandatory Human Approval Context Fields (§66):
  who: {
    agentId: string;
    agentName: string;
    principalId: string;
    principalName: string;
    organization: string;
  };
  what: {
    actionType: string;
    actionDescription: string;
    domain: string;
  };
  why: {
    intentPurpose: string;
    justification: string;
    budgetTag: string;
  };
  howMuch: {
    amountMinor: number;
    currency: string;
    formatted: string;
    exchangeRateNotice?: string;
  };
  merchant: {
    merchantId: string;
    name: string;
    isVerified: boolean;
    trustScore: number;
  };
  product: {
    productId: string;
    variantId: string;
    title: string;
    sku: string;
    category: string;
  };
  evidence: {
    priceSource: string;
    observedPriceMinor: number;
    inventorySnapshot: number;
    snapshotTimestamp: string;
    hashSha256: string;
  };
  policy: {
    policyVersion: string;
    triggeredRule: string;
    dailyLimitMinor: number;
    currentDailySpentMinor: number;
    singleActionLimitMinor: number;
  };
  risk: {
    level: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
    score: number;
    factors: Array<{ code: string; description: string; weight: number }>;
  };
  recommendation: {
    advisor: "JEV" | "DECISION_ENGINE";
    action: "ALLOW" | "REVIEW" | "DENY" | "ESCALATE";
    confidence: number;
    rationale: string;
  };
  conflicts: Array<{
    type: string;
    severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
    description: string;
    asserted: string;
    verified: string;
  }>;
}
