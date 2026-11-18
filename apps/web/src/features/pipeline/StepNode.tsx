import React from "react";
import { PipelineStep, StepStatus } from "../../types/pipeline";

interface StepNodeProps {
  step: PipelineStep;
  index: number;
  isSelected: boolean;
  tabIndex: number;
  onClick: () => void;
  onNavigate: (index: number) => void;
  stepCount: number;
}

export const StepNode: React.FC<StepNodeProps> = ({
  step,
  index,
  isSelected,
  tabIndex,
  onClick,
  onNavigate,
  stepCount,
}) => {
  const getStatusColor = (status: StepStatus) => {
    switch (status) {
      case "SUCCESS":
        return {
          bg: "bg-emerald-50/80",
          border: "border-emerald-400",
          badge: "bg-emerald-600 text-white",
          icon: "✓",
        };
      case "WARNING":
        return {
          bg: "bg-amber-50/90",
          border: "border-amber-400",
          badge: "bg-amber-500 text-white",
          icon: "!",
        };
      case "FAILED":
        return {
          bg: "bg-rose-50/90",
          border: "border-rose-400",
          badge: "bg-rose-600 text-white",
          icon: "✕",
        };
      case "RUNNING":
        return {
          bg: "bg-blue-50",
          border: "border-blue-400",
          badge: "bg-blue-600 text-white",
          icon: "↻",
        };
      case "SKIPPED":
        return {
          bg: "bg-slate-50",
          border: "border-slate-200",
          badge: "bg-slate-200 text-slate-600",
          icon: "—",
        };
      default:
        return {
          bg: "bg-white",
          border: "border-slate-200",
          badge: "bg-slate-100 text-slate-500 border border-slate-200",
          icon: `${index + 1}`,
        };
    }
  };

  const style = getStatusColor(step.status);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    let targetIndex = -1;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") {
      targetIndex = (index + 1) % stepCount;
    } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
      targetIndex = (index - 1 + stepCount) % stepCount;
    } else if (e.key === "Home") {
      targetIndex = 0;
    } else if (e.key === "End") {
      targetIndex = stepCount - 1;
    }
    if (targetIndex !== -1) {
      e.preventDefault();
      onNavigate(targetIndex);
    }
  };

  return (
    <button
      id={`pipeline-step-${index}`}
      type="button"
      tabIndex={tabIndex}
      aria-pressed={isSelected}
      aria-label={`Step ${index + 1}: ${step.title}, status ${step.status}`}
      onClick={onClick}
      onKeyDown={handleKeyDown}
      className={`relative cursor-pointer text-left transition-all duration-200 p-3.5 rounded-xl border flex flex-col justify-between w-full h-full min-h-[142px] focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-2 ${
        isSelected
          ? `ring-2 ring-blue-600 shadow-md ${style.bg} ${style.border}`
          : `hover:shadow-md hover:-translate-y-0.5 ${style.bg} ${style.border}`
      }`}
    >
      <div className="flex items-center justify-between gap-2 mb-2 w-full">
        <span className={`w-6 h-6 rounded-full text-[11px] font-bold flex items-center justify-center ${style.badge}`} aria-hidden="true">
          {style.icon}
        </span>
        <span className="text-[10px] font-mono text-slate-400">
          {step.durationMs > 0 ? `${step.durationMs}ms` : step.status}
        </span>
      </div>

      <div className="mb-1 w-full">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Step {index + 1}
        </div>
        <div className="text-xs font-bold text-slate-900 leading-snug mt-0.5" title={step.title}>
          {step.title}
        </div>
      </div>

      <div className="text-[10px] text-slate-500 line-clamp-2 mt-1.5 leading-relaxed w-full">
        {step.summary || "Pending evaluation"}
      </div>
    </button>
  );
};
