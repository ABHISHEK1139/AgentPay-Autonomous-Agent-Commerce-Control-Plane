import React, { useEffect, useState } from "react";
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

  const [approvals, setApprovals] = useState<ApprovalItem[]>([]);
  const [selectedApproval, setSelectedApproval] = useState<ApprovalItem | null>(null);
  const [isApprovalModalOpen, setIsApprovalModalOpen] = useState<boolean>(false);

  // Initialize data on mount
  useEffect(() => {
    const loadedScenarios = apiService.getScenarios();
    setScenarios(loadedScenarios);

    apiService.getApprovals().then((items) => setApprovals(items));
    apiService.getEmergencyState().then((state) => setEmergencyState(state));

    // Pre-populate with Scenario 1
    if (loadedScenarios.length > 0) {
      runScenario(loadedScenarios[0].id);
    }
  }, []);

  const runScenario = async (scenarioId: string) => {
    setActiveScenarioId(scenarioId);
    setIsRunning(true);

    if (emergencyState.isPaused) {
      // If paused, abort immediately
      const scenario = scenarios.find((s) => s.id === scenarioId) || scenarios[0];
      setExecution({
        executionId: `exec_paused_${Date.now()}`,
        scenarioId,
        actionId: `act_${scenarioId}`,
        tenantId: "tenant_corp_alpha",
        agentId: "ag_procure_42",
        amountMinor: scenario.parameters.agentAmountMinor,
        currency: scenario.parameters.currency,
        startedAt: new Date().toISOString(),
        completedAt: new Date().toISOString(),
        overallStatus: "PAUSED",
        steps: scenario.steps.map((st) => ({
          ...st,
          status: "FAILED",
          error: "EMERGENCY_PAUSE_ACTIVE: System under fail-closed emergency halt (§21, §67)",
        })),
      });
      setIsRunning(false);
      return;
    }

    try {
      const execResult = await apiService.executeScenario(scenarioId);
      setExecution(execResult);
    } finally {
      setIsRunning(false);
    }
  };

  const handlePause = async (action: EmergencyControlAction, operatorId: string, reason: string) => {
    const updated = await apiService.triggerEmergencyPause(operatorId, `${action}: ${reason}`);
    setEmergencyState(updated);
    if (execution) {
      setExecution({
        ...execution,
        overallStatus: "PAUSED",
      });
    }
  };

  const handleResume = async () => {
    const updated = await apiService.triggerEmergencyResume("sec_admin");
    setEmergencyState(updated);
  };

  const handleApprove = async (id: string, reason: string) => {
    const updated = await apiService.resolveApproval(id, "APPROVED", "current_user", reason);
    setApprovals((prev) => prev.map((a) => (a.id === id ? updated : a)));
  };

  const handleReject = async (id: string, reason: string) => {
    const updated = await apiService.resolveApproval(id, "REJECTED", "current_user", reason);
    setApprovals((prev) => prev.map((a) => (a.id === id ? updated : a)));
  };

  const handleOpenApproval = (item: ApprovalItem) => {
    setSelectedApproval(item);
    setIsApprovalModalOpen(true);
  };

  return (
    <div className="min-h-screen bg-slate-100 flex flex-col font-sans text-slate-800">
      <Navbar
        activeTab={activeTab}
        onTabChange={setActiveTab}
        isPaused={emergencyState.isPaused}
        onPause={handlePause}
        onResume={handleResume}
        isDemoMode={isDemoMode}
        onToggleDemoMode={() => {
          const next = !isDemoMode;
          setIsDemoMode(next);
          apiService.setDemoMode(next);
        }}
      />

      <EmergencyBanner state={emergencyState} onResume={handleResume} />

      <main
        id={`panel-${activeTab}`}
        role="tabpanel"
        aria-labelledby={`tab-${activeTab}`}
        className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6"
      >
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

      <footer className="bg-slate-900 border-t border-slate-800 py-4 text-center text-xs text-slate-500 font-mono">
        AgentPay Control Plane • Sprint 10 & 11 Verified • Clean Architecture Domain Purity (§10) •
        Integer Minor Units (§122) • Fail-Closed Deterministic Governance (§2.4)
      </footer>
    </div>
  );
};
