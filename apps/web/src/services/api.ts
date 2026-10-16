import type { ApprovalItem } from "../types/approval.ts";
import type { EmergencyState } from "../types/emergency.ts";
import type { PipelineExecution, PipelineStep } from "../types/pipeline.ts";
import type { DemoScenario } from "../types/scenarios.ts";
import { INITIAL_EMERGENCY_STATE, MOCK_APPROVAL_ITEMS } from "./mockData.ts";
import { DEMO_SCENARIOS } from "./scenarioData.ts";

export class ControlCenterApiService {
  private _demoMode: boolean = true;
  private _baseUrl: string = "";
  // NOTE: no default credential. The UI must call setAuthToken() after the
  // operator signs in; shipping a hardcoded admin token would grant anyone
  // with the built bundle full operator access.
  private _authToken: string = "";
  private _tenantId: string = "tenant_01";
  private _emergencyState: EmergencyState = { ...INITIAL_EMERGENCY_STATE };
  private _approvals: ApprovalItem[] = [...MOCK_APPROVAL_ITEMS];

  public get isDemoMode(): boolean {
    return this._demoMode;
  }

  public setDemoMode(value: boolean): void {
    this._demoMode = value;
  }

  public get baseUrl(): string {
    return this._baseUrl;
  }

  public setBaseUrl(url: string): void {
    this._baseUrl = url.replace(/\/+$/, "");
  }

  public get authToken(): string {
    return this._authToken;
  }

  public setAuthToken(token: string): void {
    this._authToken = token;
  }

  public get tenantId(): string {
    return this._tenantId;
  }

  public setTenantId(tenantId: string): void {
    this._tenantId = tenantId;
  }

  public buildHeaders(customHeaders: Record<string, string> = {}): Record<string, string> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-Tenant-ID": this._tenantId,
      ...customHeaders,
    };
    if (this._authToken) {
      headers["Authorization"] = `Bearer ${this._authToken}`;
    }
    return headers;
  }

  public async getEmergencyState(): Promise<EmergencyState> {
    if (!this._demoMode) {
      try {
        const res = await fetch(`${this._baseUrl}/api/v1/emergency/status`, {
          headers: this.buildHeaders(),
        });
        if (res.ok) {
          const body = await res.json();
          this._emergencyState = {
            isPaused: Boolean(body.paused ?? body.emergency_paused),
            pausedAt: body.paused_at,
            pausedBy: body.operator_id,
            reason: body.reason,
            scope: "GLOBAL",
          };
          return { ...this._emergencyState };
        }
      } catch {
        // Fallback to local state if server unreachable
      }
    }
    return { ...this._emergencyState };
  }

  public async triggerEmergencyPause(operatorId: string, reason: string): Promise<EmergencyState> {
    if (!this._demoMode) {
      try {
        const res = await fetch(`${this._baseUrl}/api/v1/emergency/pause`, {
          method: "POST",
          headers: this.buildHeaders(),
          body: JSON.stringify({ operator_id: operatorId, reason }),
        });
        if (res.ok) {
          const body = await res.json();
          this._emergencyState = {
            isPaused: Boolean(body.emergency_paused ?? body.paused ?? true),
            pausedAt: body.paused_at || new Date().toISOString(),
            pausedBy: operatorId,
            reason,
            scope: "GLOBAL",
          };
          return { ...this._emergencyState };
        }
      } catch {
        // Fallback
      }
    }
    this._emergencyState = {
      isPaused: true,
      pausedAt: new Date().toISOString(),
      pausedBy: operatorId,
      reason,
      scope: "GLOBAL",
    };
    return { ...this._emergencyState };
  }

  public async triggerEmergencyResume(operatorId: string): Promise<EmergencyState> {
    if (!this._demoMode) {
      try {
        const res = await fetch(`${this._baseUrl}/api/v1/emergency/resume`, {
          method: "POST",
          headers: this.buildHeaders(),
          body: JSON.stringify({ operator_id: operatorId }),
        });
        if (res.ok) {
          this._emergencyState = {
            isPaused: false,
            scope: "GLOBAL",
          };
          return { ...this._emergencyState };
        }
      } catch {
        // Fallback
      }
    }
    this._emergencyState = {
      isPaused: false,
      scope: "GLOBAL",
    };
    return { ...this._emergencyState };
  }

  public getScenarios(): DemoScenario[] {
    return DEMO_SCENARIOS;
  }

  public async getApprovals(): Promise<ApprovalItem[]> {
    if (!this._demoMode) {
      try {
        const res = await fetch(`${this._baseUrl}/api/v1/approvals`, {
          headers: this.buildHeaders(),
        });
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data.approvals) && data.approvals.length > 0) {
            return data.approvals.map((backendApp: any) => {
              const existing = this._approvals.find(
                (m) => m.id === backendApp.approval_id || m.actionId === backendApp.action_id
              );
              if (existing) {
                return {
                  ...existing,
                  id: backendApp.approval_id,
                  status: backendApp.status,
                  howMuch: {
                    ...existing.howMuch,
                    amountMinor: backendApp.amount_minor ?? existing.howMuch.amountMinor,
                    currency: backendApp.currency ?? existing.howMuch.currency,
                  },
                };
              }
              return {
                id: backendApp.approval_id,
                status: backendApp.status || "PENDING",
                actionId: backendApp.action_id || `act_${backendApp.approval_id}`,
                createdAt: new Date().toISOString(),
                who: { principalId: "usr_buyer_42", agentId: "ag_procure_01", tenantId: this._tenantId },
                what: { domain: "commerce", actionType: "purchase" },
                why: { intentPurpose: "Autonomous procurement requisition" },
                howMuch: { amountMinor: backendApp.amount_minor || 500000, currency: backendApp.currency || "INR" },
                merchant: { id: "merch_acme", name: "Acme Supplies Ltd" },
                product: { productId: "prod_01", variantId: "var_01", title: "Enterprise Hardware" },
                evidence: {
                  priceSource: "catalog",
                  hashSha256: "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                  gatheredAt: new Date().toISOString(),
                },
                policy: { triggeredRule: "Rule: Spending limit exceeded", confidence: 0.98 },
                risk: { level: "MEDIUM", score: 0.45 },
                recommendation: { advisor: "JEV Engine", action: "REQUIRE_HUMAN_APPROVAL" },
                conflicts: [],
              };
            });
          }
        }
      } catch {
        // Fallback to local approvals on network error
      }
    }
    return [...this._approvals];
  }

  public async resolveApproval(
    approvalId: string,
    decision: "APPROVED" | "REJECTED",
    approverId: string,
    reason?: string
  ): Promise<ApprovalItem> {
    if (!this._demoMode) {
      try {
        const subAction = decision === "APPROVED" ? "approve" : "reject";
        await fetch(
          `${this._baseUrl}/api/v1/approvals/${encodeURIComponent(approvalId)}/${subAction}`,
          {
            method: "POST",
            headers: this.buildHeaders(),
            body: JSON.stringify({ approver_id: approverId, reason: reason || "Control Center Decision" }),
          }
        );
      } catch {
        // Fallback
      }
    }

    const itemIndex = this._approvals.findIndex((a) => a.id === approvalId);
    if (itemIndex === -1) {
      throw new Error(`Approval '${approvalId}' not found`);
    }
    const updated: ApprovalItem = {
      ...this._approvals[itemIndex],
      status: decision,
      policy: {
        ...this._approvals[itemIndex].policy,
        triggeredRule: `${this._approvals[itemIndex].policy.triggeredRule} [${decision} by ${approverId}: ${reason || "Verified"}]`,
      },
    };
    this._approvals[itemIndex] = updated;
    return updated;
  }

  public async getMetrics(): Promise<Record<string, any>> {
    if (!this._demoMode) {
      try {
        const res = await fetch(`${this._baseUrl}/api/v1/metrics`, {
          headers: this.buildHeaders(),
        });
        if (res.ok) {
          return await res.json();
        }
      } catch {
        // Fallback
      }
    }
    return { status: "healthy", metrics: {}, demo: true };
  }

  public async executeAction(payload: Record<string, any>): Promise<Record<string, any>> {
    if (!this._demoMode) {
      try {
        const res = await fetch(`${this._baseUrl}/api/v1/actions`, {
          method: "POST",
          headers: this.buildHeaders(),
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          return await res.json();
        }
      } catch {
        // Fallback
      }
    }
    return { status: "COMPLETED", decision: "ALLOW", order_id: `ord_${Date.now()}` };
  }

  public async getCatalog(): Promise<Record<string, any>> {
    if (!this._demoMode) {
      try {
        const res = await fetch(`${this._baseUrl}/agent/catalog`, {
          headers: this.buildHeaders(),
        });
        if (res.ok) {
          return await res.json();
        }
      } catch {
        // Fallback
      }
    }
    return { products: [] };
  }

  public executeScenario(
    scenarioId: string,
    onStepUpdate?: (step: PipelineStep, index: number) => void
  ): Promise<PipelineExecution> {
    const scenario = DEMO_SCENARIOS.find((s) => s.id === scenarioId) || DEMO_SCENARIOS[0];
    const executionId = `exec_${scenario.id}_${Date.now()}`;
    const startedAt = new Date().toISOString();

    return new Promise((resolve) => {
      const stepsCopy: PipelineStep[] = scenario.steps.map((st) => ({
        ...st,
        status: "IDLE",
        timestamp: undefined,
      }));

      let currentStepIndex = 0;
      const stepInterval = setInterval(() => {
        if (currentStepIndex < stepsCopy.length) {
          const originalStep = scenario.steps[currentStepIndex];
          const updatedStep: PipelineStep = {
            ...originalStep,
            status: originalStep.status,
            timestamp: new Date().toISOString(),
          };
          stepsCopy[currentStepIndex] = updatedStep;
          if (onStepUpdate) {
            onStepUpdate(updatedStep, currentStepIndex);
          }
          currentStepIndex++;
        } else {
          clearInterval(stepInterval);
          const hasFailure = stepsCopy.some((s) => s.status === "FAILED");
          const hasReview = stepsCopy.some((s) => s.status === "WARNING");
          const overallStatus = hasFailure ? "FAILED" : hasReview ? "BLOCKED" : "COMPLETED";

          resolve({
            executionId,
            scenarioId: scenario.id,
            actionId: `act_${scenario.id}`,
            tenantId: "tenant_corp_alpha",
            agentId: "ag_procure_42",
            amountMinor: scenario.parameters.agentAmountMinor,
            currency: scenario.parameters.currency,
            startedAt,
            completedAt: new Date().toISOString(),
            overallStatus,
            steps: stepsCopy,
          });
        }
      }, 350);
    });
  }
}

export const apiService = new ControlCenterApiService();
