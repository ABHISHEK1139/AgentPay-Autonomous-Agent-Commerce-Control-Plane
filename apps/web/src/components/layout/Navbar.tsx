import React from "react";
import { KillSwitchButton } from "../../features/emergency/KillSwitchButton";
import { EmergencyControlAction } from "../../types/emergency";

interface NavbarProps {
  activeTab: string;
  onTabChange: (tab: string) => void;
  isPaused: boolean;
  onPause: (action: EmergencyControlAction, operatorId: string, reason: string) => void;
  onResume: () => void;
  isDemoMode: boolean;
  onToggleDemoMode: () => void;
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
    { id: "visualizer", label: "Pipeline Visualizer (§111)" },
    { id: "scenarios", label: "Scenarios 1–8 (§112)" },
    { id: "approvals", label: "Human Approvals (§66)" },
    { id: "buyer", label: "Buyer Area" },
    { id: "merchant", label: "Merchant Area" },
    { id: "org", label: "Organization" },
    { id: "security", label: "Security & Incidents" },
  ];

  return (
    <header className="bg-slate-900 border-b border-slate-800 text-white sticky top-0 z-40 shadow-md">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16 gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 flex items-center justify-center font-black text-white text-base shadow-sm">
              AP
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-extrabold text-base tracking-tight text-white">AgentPay</span>
                <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-blue-900/60 text-blue-300 border border-blue-700/60 font-semibold">
                  Control Center
                </span>
              </div>
              <span className="text-[11px] text-slate-400 block -mt-0.5">
                Autonomous Commerce Control Plane
              </span>
            </div>
          </div>

          <div className="flex items-center gap-3">
            {/* Live vs Demo Mode Toggle */}
            <button
              onClick={onToggleDemoMode}
              className="text-xs px-2.5 py-1 rounded-lg border border-slate-700 hover:border-slate-600 bg-slate-800/80 text-slate-300 font-mono transition-colors flex items-center gap-1.5"
              title="Toggle between deterministic offline demo simulation and live ASGI backend"
            >
              <span
                className={`w-2 h-2 rounded-full ${
                  isDemoMode ? "bg-amber-400" : "bg-emerald-400 animate-pulse"
                }`}
              ></span>
              <span>{isDemoMode ? "Mode: Demo Simulation" : "Mode: Live ASGI"}</span>
            </button>

            {/* Emergency Kill Switch (§21, §67) */}
            <KillSwitchButton isPaused={isPaused} onPause={onPause} onResume={onResume} />
          </div>
        </div>

        {/* Tab Navigation */}
        <div className="flex space-x-1 overflow-x-auto py-2 border-t border-slate-800/80 scrollbar-none">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onTabChange(tab.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold whitespace-nowrap transition-all ${
                activeTab === tab.id
                  ? "bg-blue-600 text-white shadow-sm"
                  : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>
    </header>
  );
};
