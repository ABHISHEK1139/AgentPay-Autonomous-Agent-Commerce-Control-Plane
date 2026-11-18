import React, { useState } from "react";
import { Button } from "../../components/ui/Button";
import { EmergencyControlAction } from "../../types/emergency";
import { EmergencyControlsModal } from "./EmergencyControlsModal";

interface KillSwitchButtonProps {
  isPaused: boolean;
  onPause: (
    action: EmergencyControlAction,
    operatorId: string,
    reason: string
  ) => void | Promise<void>;
  onResume: () => void | Promise<void>;
}

export const KillSwitchButton: React.FC<KillSwitchButtonProps> = ({
  isPaused,
  onPause,
  onResume,
}) => {
  const [modalOpen, setModalOpen] = useState(false);

  if (isPaused) {
    return (
      <Button
        variant="warning"
        size="sm"
        className="font-bold text-[11px] tracking-wide uppercase bg-amber-500 hover:bg-amber-600 border border-amber-600 shadow-md"
        onClick={onResume}
        aria-pressed="true"
        aria-label="Emergency pause active: Resume all transactions"
      >
        RESUME ALL TRANSACTIONS
      </Button>
    );
  }

  return (
    <>
      <Button
        variant="danger"
        size="sm"
        className="font-bold text-[11px] tracking-wide uppercase shadow-md shadow-rose-950/30 border border-rose-400/70 hover:bg-rose-500"
        onClick={() => setModalOpen(true)}
        aria-pressed="false"
        aria-haspopup="dialog"
        aria-label="Trigger emergency kill switch: pause all agent transactions per section 21 and 67"
      >
        <span className="hidden xl:inline">PAUSE ALL AGENT TRANSACTIONS (§67)</span>
        <span className="xl:hidden">PAUSE AGENTS</span>
      </Button>

      <EmergencyControlsModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onConfirm={onPause}
      />
    </>
  );
};
