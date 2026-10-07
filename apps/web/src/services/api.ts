import type { ApprovalItem } from "../types/approval.ts";
import type { EmergencyState } from "../types/emergency.ts";
import type { PipelineExecution, PipelineStep } from "../types/pipeline.ts";
import type { DemoScenario } from "../types/scenarios.ts";
import { INITIAL_EMERGENCY_STATE, MOCK_APPROVAL_ITEMS } from "./mockData.ts";
import { DEMO_SCENARIOS } from "./scenarioData.ts";

class ControlCenterApiService {
  private _demoMode: boolean = true;
  private _emergencyState: EmergencyState = { ...INITIAL_EMERGENCY_STATE };
  private _approvals: ApprovalItem[] = [...MOCK_APPROVAL_ITEMS];

  public get isDemoMode(): boolean {
    return this._demoMode;
  }

  public setDemoMode(value: boolean): void {
    this._demoMode = value;
  }

  public async getEmergencyState(): Promise<EmergencyState> {
    if (!this._demoMode) {
      try {
        const res = await fetch("/api/v1/health");
        if (res.ok) {
          const body = await res.json();
          return {
            isPaused: body.status !== "healthy",
            scope: "GLOBAL",
          };
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
        const res = await fetch("/api/v1/emergency/pause", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ operator_id: operatorId, reason }),
        });
        if (res.ok) {
          const body = await res.json();
          this._emergencyState = {
            isPaused: body.emergency_paused,
            pausedAt: new Date().toISOString(),
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
        const res = await fetch("/api/v1/emergency/resume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
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
    return [...this._approvals];
  }

  public async resolveApproval(
    approvalId: string,
    decision: "APPROVED" | "REJECTED",
    approverId: string,
    reason?: string
  ): Promise<ApprovalItem> {
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

  public executeScenario(scenarioId: string, onStepUpdate?: (step: PipelineStep, index: number) => void): Promise<PipelineExecution> {
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
