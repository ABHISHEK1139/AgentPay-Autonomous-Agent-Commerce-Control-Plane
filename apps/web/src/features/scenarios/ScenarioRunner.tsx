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
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-4 pb-3 border-b border-slate-100">
        <div>
          <h2 className="text-base font-bold text-slate-900">
            Interactive Demo Scenarios 1–8 (§112)
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Select and trigger real-time simulated transactions demonstrating policy, approval, and tamper defenses.
          </p>
        </div>
        <div className="text-xs font-mono text-slate-500 bg-slate-50 px-3 py-1 rounded-md border border-slate-200">
          8 Pre-Engineered Test Cases
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
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
    </div>
  );
};
