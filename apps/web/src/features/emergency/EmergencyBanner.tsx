import React from "react";
import { Button } from "../../components/ui/Button";
import { EmergencyState } from "../../types/emergency";

interface EmergencyBannerProps {
  state: EmergencyState;
  onResume: () => void;
}

export const EmergencyBanner: React.FC<EmergencyBannerProps> = ({ state, onResume }) => {
  if (!state.isPaused) return null;

  return (
    <div className="bg-rose-600 text-white px-6 py-3.5 shadow-lg border-b border-rose-700 animate-pulse">
      <div className="max-w-7xl mx-auto flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="w-8 h-8 rounded-full bg-white text-rose-700 font-black text-lg flex items-center justify-center shadow-sm">
            !
          </span>
          <div>
            <div className="font-black text-sm tracking-wide uppercase">
              EMERGENCY KILL SWITCH ENGAGED — AUTONOMOUS TRANSACTIONS HALTED (§21, §67)
            </div>
            <div className="text-xs text-rose-100 font-mono mt-0.5">
              Scope: {state.scope} • Operator: {state.pausedBy || "Security Admin"} • Reason:{" "}
              {state.reason || "Manual containment protocol"} • Fail-Closed Active
            </div>
          </div>
        </div>

        <Button
          variant="secondary"
          size="sm"
          className="bg-white text-rose-750 hover:bg-rose-50 border-0 font-bold shadow"
          onClick={onResume}
        >
          Resume System Operations
        </Button>
      </div>
    </div>
  );
};
