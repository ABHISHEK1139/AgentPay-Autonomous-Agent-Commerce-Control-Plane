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
      <div className="bg-white rounded-xl border border-slate-200 p-8 text-center text-slate-500">
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
        return { variant: "success", label: "TRANSACTION ALLOWED & COMPLETED" };
      case "BLOCKED":
        return { variant: "warning", label: "HALTED FOR HUMAN APPROVAL (§66)" };
      case "FAILED":
        return { variant: "danger", label: "BLOCKED BY POLICY / INTEGRITY FAIL-CLOSED (§2.4)" };
      case "RUNNING":
        return { variant: "info", label: "PIPELINE EXECUTING..." };
      case "PAUSED":
        return { variant: "danger", label: "SYSTEM EMERGENCY PAUSED (§21, §67)" };
    }
  };

  const badge = getOverallBadge(execution.overallStatus);

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-4 pb-4 mb-4 border-b border-slate-100">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-base font-bold text-slate-900">
              Autonomous Transaction Pipeline (§111)
            </h2>
            <Badge variant={badge.variant}>{badge.label}</Badge>
          </div>
          <p className="text-xs text-slate-500 mt-0.5 font-mono">
            Action: <span className="text-slate-800 font-semibold">{execution.actionId}</span> • Tenant:{" "}
            <span className="text-slate-800">{execution.tenantId}</span> • Agent:{" "}
            <span className="text-slate-800">{execution.agentId}</span>
          </p>
        </div>

        <div className="flex items-center gap-3 text-xs font-mono">
          <div className="bg-slate-50 px-3 py-1.5 rounded-lg border border-slate-200 text-slate-700">
            Amount: <span className="font-bold text-slate-900">₹{(execution.amountMinor / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}</span>
          </div>
          <div className="bg-slate-50 px-3 py-1.5 rounded-lg border border-slate-200 text-slate-700">
            Total Latency:{" "}
            <span className="font-bold text-slate-900">
              {execution.steps.reduce((acc, s) => acc + s.durationMs, 0)}ms
            </span>
          </div>
        </div>
      </div>

      {/* 10-Step Horizontal Scrollable or Wrapped Grid */}
      <div className="overflow-x-auto pb-2">
        <div className="flex items-stretch gap-2 min-w-[1300px]">
          {execution.steps.map((step, idx) => (
            <React.Fragment key={step.id}>
              <StepNode
                step={step}
                index={idx}
                isSelected={selectedStepIndex === idx}
                onClick={() => setSelectedStepIndex(idx)}
              />
              {idx < execution.steps.length - 1 && (
                <div className="flex items-center justify-center text-slate-300 px-0.5 select-none font-bold text-xs">
                  →
                </div>
              )}
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* Detail Inspection Drawer */}
      {selectedStep && (
        <StepDrawer
          step={selectedStep}
          stepIndex={selectedStepIndex ?? 0}
          onClose={() => setSelectedStepIndex(null)}
        />
      )}
    </div>
  );
};
