import React from "react";
import { KillSwitchButton } from "../../features/emergency/KillSwitchButton";
import { EmergencyControlAction } from "../../types/emergency";

interface NavbarProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
  isPaused: boolean;
  onPause: (
    action: EmergencyControlAction,
    operatorId: string,
    reason: string
  ) => void | Promise<void>;
  onResume: () => void | Promise<void>;
  isDemoMode: boolean;
  onToggleDemoMode: () => void | Promise<void>;
}

export const Navbar: React.FC<NavbarProps> = ({
  activeTab,
  onTabChange,
  isPaused,
  onPause,
  onResume,
  isDemoMode,
  onToggleDemoMode,
}) => {
  const tabs = [
    { id: "visualizer", label: "Pipeline" },
    { id: "scenarios", label: "Scenarios 1–8" },
    { id: "approvals", label: "Approvals" },
    { id: "buyer", label: "Buyer" },
    { id: "merchant", label: "Merchant" },
    { id: "org", label: "Organization" },
    { id: "security", label: "Security" },
  ];

  const handleTabKeyDown = (e: React.KeyboardEvent, index: number) => {
    let targetIndex = -1;
    if (e.key === "ArrowRight") {
      targetIndex = (index + 1) % tabs.length;
    } else if (e.key === "ArrowLeft") {
      targetIndex = (index - 1 + tabs.length) % tabs.length;
    } else if (e.key === "Home") {
      targetIndex = 0;
    } else if (e.key === "End") {
      targetIndex = tabs.length - 1;
    }

    if (targetIndex !== -1) {
      e.preventDefault();
      const targetTab = tabs[targetIndex];
      onTabChange(targetTab.id);
      document.getElementById(`tab-${targetTab.id}`)?.focus();
    }
  };

  return (
    <header className="bg-slate-950/95 backdrop-blur-xl border-b border-slate-800 text-white sticky top-0 z-40 shadow-lg shadow-slate-950/10">
      <div className="max-w-[1440px] mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex flex-wrap items-center justify-between gap-3 py-3">
          <div className="flex items-center gap-3 min-w-0">
            <div
              className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 via-blue-600 to-indigo-600 flex items-center justify-center shadow-lg shadow-blue-950/40 ring-1 ring-blue-400/40"
              aria-hidden="true"
            >
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.8}
                  d="M12 3l7 3v5c0 4.6-2.9 8.2-7 10-4.1-1.8-7-5.4-7-10V6l7-3z"
                />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} d="M9 12l2 2 4-4" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-extrabold text-lg tracking-tight text-white">AgentPay</span>
                <span className="hidden sm:inline text-[10px] uppercase font-mono px-2 py-0.5 rounded-md bg-blue-950/70 text-blue-300 border border-blue-800/70 font-semibold tracking-wider">
                  Control Center
                </span>
              </div>
              <span className="text-[11px] text-slate-400 block truncate">
                Govern every agent action before money moves
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2 sm:gap-3 ml-auto">
            <button
              type="button"
              onClick={onToggleDemoMode}
              className="text-xs px-3 py-2 rounded-xl border border-slate-700 hover:border-slate-500 bg-slate-900/90 text-slate-200 font-medium transition-colors flex items-center gap-2 focus:outline-none focus:ring-2 focus:ring-blue-400"
              title="Switch live API data for supported actions. The scenario laboratory always remains a local simulation."
              aria-label={`Toggle simulation mode, currently ${isDemoMode ? "Demo Simulation" : "Live API data"}`}
            >
              <span
                className={`w-2 h-2 rounded-full ${isDemoMode ? "bg-amber-400" : "bg-emerald-400"}`}
                aria-hidden="true"
              />
              <span>{isDemoMode ? "Demo data" : "Live API data"}</span>
            </button>

            <KillSwitchButton isPaused={isPaused} onPause={onPause} onResume={onResume} />
          </div>
        </div>

        <nav
          role="tablist"
          aria-label="Control Center Dashboard Sections"
          className="flex gap-1 overflow-x-auto pb-2 scrollbar-none"
        >
          {tabs.map((tab, idx) => (
            <button
              key={tab.id}
              id={`tab-${tab.id}`}
              role="tab"
              tabIndex={activeTab === tab.id ? 0 : -1}
              aria-selected={activeTab === tab.id}
              aria-controls={`panel-${tab.id}`}
              onClick={() => onTabChange(tab.id)}
              onKeyDown={(e) => handleTabKeyDown(e, idx)}
              className={`px-3.5 py-2 rounded-lg text-xs font-semibold whitespace-nowrap transition-colors focus:outline-none focus:ring-2 focus:ring-blue-400 ${
                activeTab === tab.id
                  ? "bg-blue-600 text-white shadow-md shadow-blue-950/30"
                  : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/80"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </nav>
      </div>
    </header>
  );
};
