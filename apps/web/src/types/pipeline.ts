export type StepStatus = "IDLE" | "RUNNING" | "SUCCESS" | "WARNING" | "FAILED" | "SKIPPED";

export type PipelineStepId =
  | "USER_INTENT"
  | "AGENT_IDENTITY"
  | "PRODUCT_DISCOVERY"
  | "EVIDENCE"
  | "RECOMMENDATION"
  | "JEV"
  | "POLICY"
  | "APPROVAL"
  | "PAYMENT"
  | "ORDER";

export interface PipelineStep {
  id: PipelineStepId;
  title: string;
  category: "INTENT" | "IDENTITY" | "COMMERCE" | "INTELLIGENCE" | "GOVERNANCE" | "EXECUTION";
  status: StepStatus;
  summary: string;
  durationMs: number;
  timestamp?: string;
  error?: string;
  data: Record<string, unknown>;
}

export interface PipelineExecution {
  executionId: string;
  scenarioId?: string;
  actionId: string;
  tenantId: string;
  agentId: string;
  amountMinor: number;
  currency: string;
  startedAt: string;
  completedAt?: string;
  overallStatus: "RUNNING" | "COMPLETED" | "BLOCKED" | "FAILED" | "PAUSED";
  steps: PipelineStep[];
}
