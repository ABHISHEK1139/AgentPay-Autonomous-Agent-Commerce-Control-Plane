import React, { useState } from "react";
import { Badge, BadgeVariant } from "../../components/ui/Badge";
import { PipelineExecution, PipelineStep } from "../../types/pipeline";
import { StepDrawer } from "./StepDrawer";
import { StepNode } from "./StepNode";

interface PipelineVisualizerProps {
  execution: PipelineExecution | null;
}

export const PipelineVisualizer: React.FC<PipelineVisualizerProps> = ({ execution }) => {
  const [selectedStepIndex, setSelectedStepIndex] = useState<number | null>(0);

  if (!execution) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200 p-8 text-center text-slate-500 shadow-sm">
        Select or run a scenario to inspect the 10-step pipeline.
      </div>
    );
  }

  const selectedStep: PipelineStep | null =
    selectedStepIndex !== null && execution.steps[selectedStepIndex]
      ? execution.steps[selectedStepIndex]
      : null;

  const getOverallBadge = (status: PipelineExecution["overallStatus"]): { variant: BadgeVariant; label: string } => {
    switch (status) {
      case "COMPLETED":
        return { variant: "success", label: "ALLOWED & COMPLETED" };
      case "BLOCKED":
        return execution.scenarioId === "scen_6"
          ? { variant: "info", label: "PAYMENT UNKNOWN — RECONCILIATION REQUIRED (§43)" }
          : { variant: "warning", label: "HALTED FOR HUMAN APPROVAL (§66)" };
      case "FAILED":
        return { variant: "danger", label: "BLOCKED FAIL-CLOSED (§2.4)" };
      case "RUNNING":
        return { variant: "info", label: "SIMULATION RUNNING…" };
      case "PAUSED":
        return { variant: "danger", label: "SYSTEM EMERGENCY PAUSED (§21, §67)" };
    }
  };

  const navigateToStep = (index: number) => {
    setSelectedStepIndex(index);
    document.getElementById(`pipeline-step-${index}`)?.focus();
  };

  const formatMoney = () =>
    new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: execution.currency,
      minimumFractionDigits: 2,
    }).format(execution.amountMinor / 100);

  const badge = getOverallBadge(execution.overallStatus);
  const completedSteps = execution.steps.filter((step) => step.status !== "IDLE" && step.status !== "RUNNING").length;

  return (
    <section
      aria-labelledby="pipeline-title"
      aria-busy={execution.overallStatus === "RUNNING"}
      className="bg-white/95 rounded-2xl border border-slate-200 p-5 sm:p-6 shadow-sm"
    >
      <div className="flex flex-wrap items-start justify-between gap-4 pb-5 mb-5 border-b border-slate-100">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <h2 id="pipeline-title" className="text-lg font-bold tracking-tight text-slate-900">
              Transaction Trust Pipeline
            </h2>
            <Badge variant={badge.variant}>{badge.label}</Badge>
          </div>
          <p className="text-[11px] text-slate-500 mt-2 font-mono break-words leading-relaxed">
            {execution.actionId} • {execution.tenantId} • {execution.agentId}
          </p>
        </div>

        <div className="flex items-center gap-2 text-xs font-mono">
          <div className="bg-slate-50 px-3 py-2 rounded-xl border border-slate-200 text-slate-600">
            Amount <span className="block font-bold text-sm text-slate-900 mt-0.5">{formatMoney()}</span>
          </div>
          <div className="bg-slate-50 px-3 py-2 rounded-xl border border-slate-200 text-slate-600">
            Progress <span className="block font-bold text-sm text-slate-900 mt-0.5">{completedSteps}/10 steps</span>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 mb-3">
        <p className="text-xs text-slate-500">
          Select a step to inspect its evidence. Use arrow keys to move between steps.
        </p>
        <span className="hidden sm:inline text-[10px] font-mono uppercase tracking-wider text-slate-400">§111 Live transaction viewer</span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3" role="group" aria-label="Transaction pipeline steps">
        {execution.steps.map((step, idx) => (
          <StepNode
            key={step.id}
            step={step}
            index={idx}
            isSelected={selectedStepIndex === idx}
            tabIndex={selectedStepIndex === idx || (selectedStepIndex === null && idx === 0) ? 0 : -1}
            onClick={() => setSelectedStepIndex(idx)}
            onNavigate={navigateToStep}
            stepCount={execution.steps.length}
          />
        ))}
      </div>

      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {selectedStep && selectedStepIndex !== null
          ? `Step ${selectedStepIndex + 1}, ${selectedStep.title}, ${selectedStep.status}`
          : "No pipeline step selected"}
      </p>

      {selectedStep && (
        <StepDrawer
          step={selectedStep}
          stepIndex={selectedStepIndex ?? 0}
          onClose={() => setSelectedStepIndex(null)}
        />
      )}
    </section>
  );
};
