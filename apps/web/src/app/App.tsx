import React, { useEffect, useRef, useState } from "react";
import { Navbar } from "../components/layout/Navbar";
import { ApprovalList } from "../features/approval/ApprovalList";
import { ApprovalModal } from "../features/approval/ApprovalModal";
import { BuyerArea } from "../features/dashboard/BuyerArea";
import { MerchantArea } from "../features/dashboard/MerchantArea";
import { OrgArea } from "../features/dashboard/OrgArea";
import { SecurityArea } from "../features/dashboard/SecurityArea";
import { EmergencyBanner } from "../features/emergency/EmergencyBanner";
import { PipelineVisualizer } from "../features/pipeline/PipelineVisualizer";
import { ScenarioRunner } from "../features/scenarios/ScenarioRunner";
import { apiService } from "../services/api";
import { ApprovalItem } from "../types/approval";
import { EmergencyControlAction, EmergencyState } from "../types/emergency";
import { PipelineExecution } from "../types/pipeline";
import { DemoScenario } from "../types/scenarios";

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<string>("visualizer");
  const [emergencyState, setEmergencyState] = useState<EmergencyState>({
    isPaused: false,
    scope: "GLOBAL",
  });
  const [isDemoMode, setIsDemoMode] = useState<boolean>(true);
  const [scenarios, setScenarios] = useState<DemoScenario[]>([]);
  const [activeScenarioId, setActiveScenarioId] = useState<string>("scen_1");
  const [execution, setExecution] = useState<PipelineExecution | null>(null);
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [actionError, setActionError] = useState<string>("");
  const runSequence = useRef(0);

  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [selectedApproval, setSelectedApproval] = useState<ApprovalItem | null>(null);
  const [isApprovalModalOpen, setIsApprovalModalOpen] = useState<boolean>(false);

  useEffect(() => {
    const loadedScenarios = apiService.getScenarios();
    setScenarios(loadedScenarios);

    void apiService.getApprovals().then((items) => setApprovals(items));
    void apiService.getEmergencyState().then((state) => setEmergencyState(state));

    if (loadedScenarios.length > 0) {
      void runScenario(loadedScenarios[0].id);
    }
  }, []);

  const runScenario = async (scenarioId: string) => {
    const availableScenarios = scenarios.length > 0 ? scenarios : apiService.getScenarios();
    const scenario = availableScenarios.find((s) => s.id === scenarioId) ?? availableScenarios[0];
    if (!scenario) return;

    const runId = ++runSequence.current;
    const executionId = `exec_${scenario.id}_${Date.now()}`;
    const startedAt = new Date().toISOString();
    setActiveScenarioId(scenario.id);
    setIsRunning(true);
    setActionError("");
    setExecution({
      executionId,
      scenarioId: scenario.id,
      actionId: `act_${scenario.id}`,
      tenantId: "tenant_corp_alpha",
      agentId: "ag_procure_42",
      amountMinor: scenario.parameters.agentAmountMinor,
      currency: scenario.parameters.currency,
      startedAt,
      overallStatus: "RUNNING",
      steps: scenario.steps.map((step) => ({
        ...step,
        status: "IDLE",
        timestamp: undefined,
      })),
    });

    if (emergencyState.isPaused) {
      setExecution({
        executionId,
        scenarioId: scenario.id,
        actionId: `act_${scenario.id}`,
        tenantId: "tenant_corp_alpha",
        agentId: "ag_procure_42",
        amountMinor: scenario.parameters.agentAmountMinor,
        currency: scenario.parameters.currency,
        startedAt,
        completedAt: new Date().toISOString(),
        overallStatus: "PAUSED",
        steps: scenario.steps.map((step) => ({
          ...step,
          status: "SKIPPED",
          error: "EMERGENCY_PAUSE_ACTIVE: System under fail-closed emergency halt (§21, §67)",
        })),
      });
      setIsRunning(false);
      return;
    }

    try {
      const execResult = await apiService.executeScenario(scenario.id, (updatedStep, index) => {
        if (runSequence.current !== runId) return;
        setExecution((current) => {
          if (!current || current.executionId !== executionId) return current;
          return {
            ...current,
            steps: current.steps.map((step, stepIndex) =>
              stepIndex === index ? updatedStep : step
            ),
          };
        });
      });
      if (runSequence.current === runId) {
        setExecution(execResult);
      }
    } catch (err) {
      if (runSequence.current === runId) {
        setActionError(err instanceof Error ? err.message : String(err));
      }
    } finally {
      if (runSequence.current === runId) {
        setIsRunning(false);
      }
    }
  };

  const handlePause = async (action: EmergencyControlAction, operatorId: string, reason: string) => {
    setActionError("");
    const updated = await apiService.triggerEmergencyPause(operatorId, `${action}: ${reason}`);
    runSequence.current += 1;
    setIsRunning(false);
    setEmergencyState(updated);
    if (execution) {
      setExecution({
        ...execution,
        overallStatus: "PAUSED",
        completedAt: new Date().toISOString(),
      });
    }
  };

  const handleResume = async () => {
    setActionError("");
    try {
      const updated = await apiService.triggerEmergencyResume("sec_admin");
      setEmergencyState(updated);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleToggleDemoMode = async () => {
    const next = !isDemoMode;
    setIsDemoMode(next);
    apiService.setDemoMode(next);
    setIsApprovalModalOpen(false);
    setActionError("");
    try {
      const [items, state] = await Promise.all([
        apiService.getApprovals(),
        apiService.getEmergencyState(),
      ]);
      setApprovals(items);
      setEmergencyState(state);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleApprove = async (id: string, reason: string) => {
    const updated = await apiService.resolveApproval(id, "APPROVED", reason);
    setApprovals((prev) => prev.map((a) => (a.id === id ? updated : a)));
  };

  const handleReject = async (id: string, reason: string) => {
    const updated = await apiService.resolveApproval(id, "REJECTED", reason);
    setApprovals((prev) => prev.map((a) => (a.id === id ? updated : a)));
  };

  const handleOpenApproval = (item: ApprovalItem) => {
    setSelectedApproval(item);
    setIsApprovalModalOpen(true);
  };

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top_left,_#e0edff_0,_transparent_32rem)] bg-slate-100 flex flex-col font-sans text-slate-800">
      <Navbar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        isPaused={emergencyState.isPaused}
        onPause={handlePause}
        onResume={handleResume}
        isDemoMode={isDemoMode}
        onToggleDemoMode={handleToggleDemoMode}
      />

      <EmergencyBanner state={emergencyState} onResume={handleResume} />

      {actionError && (
        <div className="bg-rose-50 border-b border-rose-200 px-4 py-3" role="alert">
          <div className="max-w-7xl mx-auto flex items-start justify-between gap-4 text-sm text-rose-800">
            <p>
              <span className="font-bold">Action not confirmed:</span> {actionError}
            </p>
            <button
              type="button"
              className="font-semibold underline underline-offset-2"
              onClick={() => setActionError("")}
            >
              Dismiss
            </button>
          </div>
        </div>
      )}

      <main
        id={`panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`tab-${activeTab}`}
        className="flex-1 max-w-[1440px] w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-8"
      >
        <div className="mb-6 rounded-2xl border border-slate-200/90 bg-white/85 backdrop-blur px-4 py-3.5 shadow-sm flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
          <div className="flex items-center gap-3">
            <span
              className={`h-2.5 w-2.5 rounded-full ${isDemoMode ? "bg-amber-500" : "bg-emerald-500"}`}
              aria-hidden="true"
            />
            <div>
              <p className="text-sm font-bold text-slate-900">
                {isDemoMode ? "Deterministic demo workspace" : "Live API workspace"}
              </p>
              <p className="text-xs text-slate-500 mt-0.5">
                {isDemoMode
                  ? "Local fixtures only. No real payment or model call is made."
                  : "Supported approval and emergency actions use the API; scenario runs and dashboard fixtures remain local."}
              </p>
            </div>
          </div>
          <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-slate-500">
            Policy remains deterministic
          </span>
        </div>

        {activeTab === "visualizer" && (
          <div className="space-y-6">
            <PipelineVisualizer execution={execution} />
            <ScenarioRunner
              scenarios={scenarios}
              activeScenarioId={activeScenarioId}
              isRunning={isRunning}
              onSelectScenario={runScenario}
            />
          </div>
        )}

        {activeTab === "scenarios" && (
          <div className="space-y-6">
            <ScenarioRunner
              scenarios={scenarios}
              activeScenarioId={activeScenarioId}
              isRunning={isRunning}
              onSelectScenario={runScenario}
            />
            <PipelineVisualizer execution={execution} />
          </div>
        )}

        {activeTab === "approvals" && (
          <ApprovalList approvals={approvals} onSelect={handleOpenApproval} />
        )}

        {activeTab === "buyer" && <BuyerArea />}
        {activeTab === "merchant" && <MerchantArea />}
        {activeTab === "org" && <OrgArea />}
        {activeTab === "security" && <SecurityArea />}
      </main>

      {/* Human Approval Review Modal (§66) */}
      <ApprovalModal
        item={selectedApproval}
        isOpen={isApprovalModalOpen}
        onClose={() => setIsApprovalModalOpen(false)}
        onApprove={handleApprove}
        onReject={handleReject}
      />

      <footer className="bg-slate-950 border-t border-slate-800 py-5 text-center text-xs text-slate-500">
        <div className="px-4 font-mono">
          AgentPay Control Plane • Deterministic policy boundary • Integer minor units • Fail-closed governance
        </div>
      </footer>
    </div>
  );
};
