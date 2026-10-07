import React from "react";
import { Badge, BadgeVariant } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { DemoScenario, ScenarioResultOutcome } from "../../types/scenarios";

interface ScenarioCardProps {
  scenario: DemoScenario;
  isActive: boolean;
  isRunning: boolean;
  onRun: (scenarioId: string) => void;
}

export const ScenarioCard: React.FC<ScenarioCardProps> = ({
  scenario,
  isActive,
  isRunning,
  onRun,
}) => {
  const getOutcomeBadge = (outcome: ScenarioResultOutcome): { variant: BadgeVariant; label: string } => {
    switch (outcome) {
      case "ALLOW":
        return { variant: "success", label: "OUTCOME: ALLOW" };
      case "REVIEW":
        return { variant: "warning", label: "OUTCOME: REVIEW" };
      case "DENY":
        return { variant: "danger", label: "OUTCOME: DENY" };
      case "PRICE_MISMATCH":
        return { variant: "danger", label: "PRICE MISMATCH" };
      case "INVENTORY_MISMATCH":
        return { variant: "danger", label: "INVENTORY 0" };
      case "RECONCILIATION":
        return { variant: "info", label: "RECONCILED" };
      case "EVIDENCE_CONFLICT":
        return { variant: "warning", label: "SPEC CONFLICT" };
    }
  };

  const badge = getOutcomeBadge(scenario.expectedOutcome);
  const formattedAmount = `₹${(scenario.parameters.agentAmountMinor / 100).toLocaleString("en-IN", {
    minimumFractionDigits: 2,
  })}`;

  return (
    <div
      className={`rounded-xl border p-4 transition-all duration-200 flex flex-col justify-between ${
        isActive
          ? "border-blue-500 bg-blue-50/20 shadow-md ring-1 ring-blue-500"
          : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm"
      }`}
    >
      <div>
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="text-xs font-bold text-slate-500 font-mono">
            §112 #{scenario.number}
          </span>
          <Badge variant={badge.variant}>{badge.label}</Badge>
        </div>

        <h4 className="text-sm font-bold text-slate-900 leading-snug">{scenario.title}</h4>
        <p className="text-xs text-slate-500 mt-1 line-clamp-2">{scenario.shortDescription}</p>

        <div className="mt-3 pt-3 border-t border-slate-100 flex flex-wrap gap-1.5 text-[11px] font-mono">
          <span className="bg-slate-100 text-slate-700 px-2 py-0.5 rounded border border-slate-200">
            Amount: {formattedAmount}
          </span>
          <span className="bg-slate-100 text-slate-700 px-2 py-0.5 rounded border border-slate-200">
            Limit: ₹{(scenario.parameters.autonomousLimitMinor / 100).toLocaleString("en-IN")}
          </span>
        </div>
      </div>

      <div className="mt-4 pt-2">
        <Button
          variant={isActive ? "primary" : "outline"}
          size="sm"
          className="w-full text-xs font-semibold"
          disabled={isRunning}
          onClick={() => onRun(scenario.id)}
        >
          {isRunning && isActive ? "Executing..." : isActive ? "Re-run Scenario" : "Run Scenario"}
        </Button>
      </div>
    </div>
  );
};
