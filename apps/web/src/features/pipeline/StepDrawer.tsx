import React, { useEffect } from "react";
import { Badge, BadgeVariant } from "../../components/ui/Badge";
import { PipelineStep, StepStatus } from "../../types/pipeline";

interface StepDrawerProps {
  step: PipelineStep | null;
  stepIndex: number;
  onClose: () => void;
}

export const StepDrawer: React.FC<StepDrawerProps> = ({ step, stepIndex, onClose }) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  if (!step) return null;

  const getStatusBadge = (status: StepStatus): { variant: BadgeVariant; label: string } => {
    switch (status) {
      case "SUCCESS":
        return { variant: "success", label: "PASSED / VERIFIED" };
      case "WARNING":
        return { variant: "warning", label: "POLICY REVIEW REQUIRED" };
      case "FAILED":
        return { variant: "danger", label: "BLOCKED / FAILED" };
      case "RUNNING":
        return { variant: "info", label: "PROCESSING..." };
      case "SKIPPED":
        return { variant: "neutral", label: "BYPASSED" };
      default:
        return { variant: "neutral", label: "PENDING" };
    }
  };

  const badge = getStatusBadge(step.status);

  return (
    <div
      role="region"
      aria-label={`Step ${stepIndex + 1} Execution Details`}
      className="bg-slate-900 text-slate-100 rounded-xl p-5 border border-slate-800 shadow-xl mt-4 animate-fade-in"
    >
      <div className="flex items-center justify-between pb-4 border-b border-slate-800">
        <div className="flex items-center gap-3">
          <span className="w-7 h-7 rounded-lg bg-blue-600/30 text-blue-400 font-mono text-sm font-bold flex items-center justify-center border border-blue-500/40">
            {stepIndex + 1}
          </span>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="font-bold text-white text-base">{step.title}</h3>
              <Badge variant={badge.variant}>{badge.label}</Badge>
            </div>
            <span className="text-xs text-slate-400 font-mono">
              Category: {step.category} • Latency: {step.durationMs}ms
            </span>
          </div>
        </div>

        <button
          onClick={onClose}
          aria-label="Close step details drawer"
          className="text-slate-400 hover:text-white rounded-lg p-1.5 hover:bg-slate-800 transition-colors focus:outline-none focus:ring-2 focus:ring-slate-400"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="md:col-span-1 space-y-3">
          <div>
            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Step Summary</span>
            <p className="text-sm text-slate-200 mt-1 bg-slate-800/60 p-3 rounded-lg border border-slate-700/50">
              {step.summary || "No summary recorded for this step."}
            </p>
          </div>

          {step.error && (
            <div>
              <span className="text-[11px] font-semibold uppercase tracking-wider text-rose-400">Interception / Error</span>
              <p className="text-xs text-rose-300 mt-1 bg-rose-950/40 p-3 rounded-lg border border-rose-800/60 font-mono">
                {step.error}
              </p>
            </div>
          )}

          {step.timestamp && (
            <div>
              <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">Execution Timestamp</span>
              <p className="text-xs text-slate-300 font-mono mt-1">{step.timestamp}</p>
            </div>
          )}
        </div>

        <div className="md:col-span-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-400">
            Cryptographic Payload & Verification Evidence
          </span>
          <pre className="mt-1 p-3.5 bg-slate-950 rounded-lg text-emerald-400 font-mono text-xs overflow-x-auto max-h-[220px] border border-slate-800/80">
            {JSON.stringify(step.data, null, 2)}
          </pre>
        </div>
      </div>
    </div>
  );
};
