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
        return { variant: "success", label: "EXPECTED: ALLOW" };
      case "REVIEW":
        return { variant: "warning", label: "EXPECTED: REVIEW" };
      case "DENY":
        return { variant: "danger", label: "EXPECTED: DENY" };
      case "PRICE_MISMATCH":
        return { variant: "danger", label: "EXPECTED: PRICE MISMATCH" };
      case "INVENTORY_MISMATCH":
        return { variant: "danger", label: "EXPECTED: INVENTORY 0" };
      case "RECONCILIATION":
        return { variant: "info", label: "EXPECTED: RECONCILIATION" };
      case "EVIDENCE_CONFLICT":
        return { variant: "warning", label: "EXPECTED: SPEC CONFLICT" };
    }
  };

  const formatMoney = (minorUnits: number) =>
    new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: scenario.parameters.currency,
      minimumFractionDigits: 2,
    }).format(minorUnits / 100);

  const badge = getOutcomeBadge(scenario.expectedOutcome);
  const isActiveRun = isRunning && isActive;

  return (
    <article
      aria-busy={isActiveRun}
      className={`rounded-2xl border p-4 transition-all duration-200 flex flex-col justify-between min-h-[230px] ${
        isActive
          ? "border-blue-500 bg-gradient-to-b from-blue-50 to-white shadow-lg shadow-blue-100 ring-1 ring-blue-500"
          : "border-slate-200 bg-white hover:border-blue-200 hover:shadow-md hover:-translate-y-0.5"
      }`}
    >
      <div>
        <div className="flex items-start justify-between gap-2 mb-3">
          <span className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-slate-900 text-white text-xs font-bold font-mono">
            {String(scenario.number).padStart(2, "0")}
          </span>
          <Badge variant={badge.variant} className="text-[10px] text-right">
            {badge.label}
          </Badge>
        </div>

        <h3 className="text-sm font-bold text-slate-900 leading-snug">{scenario.title}</h3>
        <p className="text-xs text-slate-500 mt-1.5 leading-relaxed line-clamp-2">
          {scenario.shortDescription}
        </p>

        <dl className="mt-4 grid grid-cols-2 gap-2 text-[11px]">
          <div className="bg-slate-50 text-slate-700 px-2.5 py-2 rounded-lg border border-slate-200">
            <dt className="text-slate-400 uppercase tracking-wide font-semibold">Amount</dt>
            <dd className="font-mono font-bold mt-0.5">{formatMoney(scenario.parameters.agentAmountMinor)}</dd>
          </div>
          <div className="bg-slate-50 text-slate-700 px-2.5 py-2 rounded-lg border border-slate-200">
            <dt className="text-slate-400 uppercase tracking-wide font-semibold">Auto limit</dt>
            <dd className="font-mono font-bold mt-0.5">{formatMoney(scenario.parameters.autonomousLimitMinor)}</dd>
          </div>
        </dl>
      </div>

      <div className="mt-4">
        <Button
          variant={isActive ? "primary" : "outline"}
          size="sm"
          className="w-full text-xs font-semibold"
          disabled={isRunning}
          onClick={() => onRun(scenario.id)}
          aria-label={`${isActive ? "Re-run" : "Run"} scenario ${scenario.number}: ${scenario.title}`}
        >
          {isActiveRun ? "Running simulation…" : isActive ? "Re-run simulation" : "Run simulation"}
        </Button>
      </div>
    </article>
  );
};
