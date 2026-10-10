import React, { useState } from "react";
import { Button } from "../../components/ui/Button";
import { EmergencyControlAction } from "../../types/emergency";
import { EmergencyControlsModal } from "./EmergencyControlsModal";

interface KillSwitchButtonProps {
  isPaused: boolean;
  onPause: (action: EmergencyControlAction, operatorId: string, reason: string) => void;
  onResume: () => void;
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
        size="md"
        className="font-black text-xs tracking-wider uppercase bg-amber-500 hover:bg-amber-600 border border-amber-600 shadow-md animate-pulse"
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
        size="md"
        className="font-black text-xs tracking-wider uppercase shadow-lg shadow-rose-600/30 ring-2 ring-rose-400 hover:ring-rose-500 animate-pulse"
        onClick={() => setModalOpen(true)}
        aria-pressed="false"
        aria-haspopup="dialog"
        aria-label="Trigger emergency kill switch: pause all agent transactions per section 21 and 67"
      >
        PAUSE ALL AGENT TRANSACTIONS (§67)
      </Button>

      <EmergencyControlsModal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onConfirm={onPause}
      />
    </>
  );
};
