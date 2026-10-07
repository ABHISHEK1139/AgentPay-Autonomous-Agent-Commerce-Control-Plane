import type { PipelineStep } from "./pipeline.ts";

export type ScenarioResultOutcome =
  | "ALLOW"
  | "REVIEW"
  | "DENY"
  | "PRICE_MISMATCH"
  | "INVENTORY_MISMATCH"
  | "RECONCILIATION"
  | "EVIDENCE_CONFLICT";

export interface DemoScenario {
  id: string;
  number: number;
  title: string;
  shortDescription: string;
  expectedOutcome: ScenarioResultOutcome;
  parameters: {
    agentAmountMinor: number;
    currency: string;
    autonomousLimitMinor: number;
    maxLimitMinor: number;
    productName: string;
    merchantName: string;
    category: string;
    agentPriceMinor?: number;
    merchantPriceMinor?: number;
    inventoryAvailable?: number;
    agentAssertedSpec?: string;
    verifiedCatalogSpec?: string;
    isMaliciousAttempt?: boolean;
    paymentFailureSimulation?: "TIMEOUT" | "NONE";
  };
  steps: PipelineStep[];
}
