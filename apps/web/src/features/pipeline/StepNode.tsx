import React from "react";
import { PipelineStep, StepStatus } from "../../types/pipeline";

interface StepNodeProps {
  step: PipelineStep;
  index: number;
  isSelected: boolean;
  onClick: () => void;
}

export const StepNode: React.FC<StepNodeProps> = ({ step, index, isSelected, onClick }) => {
  const getStatusColor = (status: StepStatus) => {
    switch (status) {
      case "SUCCESS":
        return {
          bg: "bg-emerald-50",
          border: "border-emerald-500",
          text: "text-emerald-700",
          badge: "bg-emerald-500 text-white",
          icon: "✓",
        };
      case "WARNING":
        return {
          bg: "bg-amber-50",
          border: "border-amber-500",
          text: "text-amber-700",
          badge: "bg-amber-500 text-white",
          icon: "!",
        };
      case "FAILED":
        return {
          bg: "bg-rose-50",
          border: "border-rose-500",
          text: "text-rose-700",
          badge: "bg-rose-500 text-white",
          icon: "✕",
        };
      case "RUNNING":
        return {
          bg: "bg-blue-50 animate-pulse",
          border: "border-blue-500",
          text: "text-blue-700",
          badge: "bg-blue-500 text-white",
          icon: "↻",
        };
      case "SKIPPED":
        return {
          bg: "bg-slate-50",
          border: "border-slate-300",
          text: "text-slate-400",
          badge: "bg-slate-300 text-slate-600",
          icon: "—",
        };
      default:
        return {
          bg: "bg-white",
          border: "border-slate-200",
          text: "text-slate-500",
          badge: "bg-slate-100 text-slate-400 border border-slate-200",
          icon: `${index + 1}`,
        };
    }
  };

  const style = getStatusColor(step.status);

  return (
    <div
      onClick={onClick}
      className={`relative cursor-pointer transition-all duration-200 p-3 rounded-xl border flex flex-col justify-between min-w-[130px] max-w-[150px] flex-1 ${
        isSelected
          ? "ring-2 ring-blue-600 shadow-md " + style.bg + " " + style.border
          : "hover:shadow-sm hover:border-slate-300 " + style.bg + " " + style.border
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className={`w-5 h-5 rounded-full text-[11px] font-bold flex items-center justify-center ${style.badge}`}>
          {style.icon}
        </span>
        <span className="text-[10px] font-mono text-slate-400">
          {step.durationMs > 0 ? `${step.durationMs}ms` : ""}
        </span>
      </div>

      <div className="mb-1">
        <div className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
          Step {index + 1}
        </div>
        <div className="text-xs font-bold text-slate-900 truncate" title={step.title}>
          {step.title}
        </div>
      </div>

      <div className="text-[10px] text-slate-500 line-clamp-2 mt-1 leading-tight">
        {step.summary || "Pending evaluation"}
      </div>
    </div>
  );
};
