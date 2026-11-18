import React from "react";
import { DemoScenario } from "../../types/scenarios";
import { ScenarioCard } from "./ScenarioCard";

interface ScenarioRunnerProps {
  scenarios: DemoScenario[];
  activeScenarioId: string;
  isRunning: boolean;
  onSelectScenario: (scenarioId: string) => void;
}

export const ScenarioRunner: React.FC<ScenarioRunnerProps> = ({
  scenarios,
  activeScenarioId,
  isRunning,
  onSelectScenario,
}) => {
  return (
    <section
      aria-labelledby="scenario-lab-title"
      aria-busy={isRunning}
      className="bg-white/95 rounded-2xl border border-slate-200 p-5 sm:p-6 shadow-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 mb-5 pb-4 border-b border-slate-100">
        <div>
          <div className="flex items-center gap-2">
            <h2 id="scenario-lab-title" className="text-lg font-bold tracking-tight text-slate-900">
              Deterministic Scenario Lab
            </h2>
            <span className="text-[10px] font-mono font-bold uppercase tracking-wider text-blue-700 bg-blue-50 border border-blue-200 rounded-md px-2 py-1">
              §112
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-1 max-w-2xl leading-relaxed">
            Repeatable local simulations for policy, approval, evidence, and payment failure handling. No real merchant charge is created.
          </p>
        </div>
        <div className="text-[11px] font-mono font-semibold text-slate-600 bg-slate-50 px-3 py-1.5 rounded-lg border border-slate-200">
          8 cases • Simulated only
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        {scenarios.map((scen) => (
          <ScenarioCard
            key={scen.id}
            scenario={scen}
            isActive={scen.id === activeScenarioId}
            isRunning={isRunning}
            onRun={onSelectScenario}
          />
        ))}
      </div>
      <p className="sr-only" role="status" aria-live="polite">
        {isRunning ? `Scenario ${activeScenarioId} simulation is running` : "Scenario simulation idle"}
      </p>
    </section>
  );
};
