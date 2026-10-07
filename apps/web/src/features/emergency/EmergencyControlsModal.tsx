import React, { useState } from "react";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { EmergencyControlAction } from "../../types/emergency";

interface EmergencyControlsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (action: EmergencyControlAction, operatorId: string, reason: string) => void;
}

export const EmergencyControlsModal: React.FC<EmergencyControlsModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
}) => {
  const [selectedAction, setSelectedAction] = useState<EmergencyControlAction>(
    "PAUSE_ALL_AUTONOMOUS_PAYMENTS"
  );
  const [operatorId, setOperatorId] = useState("sec_operator_01");
  const [reason, setReason] = useState("Immediate threat mitigation / anomalous spending containment");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onConfirm(selectedAction, operatorId, reason);
    onClose();
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Emergency Kill Switch Protocol (§21, §67)" maxWidth="lg">
      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-4 text-xs text-rose-800">
          <p className="font-bold text-rose-900 mb-1">WARNING: HIGH-IMPACT GOVERNANCE ACTION</p>
          Executing an emergency stop triggers an immediate fail-closed barrier across all active agent
          execution loops. The action is cryptographically recorded in the immutable audit ledger with operator
          attribution (§67).
        </div>

        <div>
          <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1.5">
            Emergency Control Scope (§21)
          </label>
          <div className="space-y-2">
            {[
              {
                id: "PAUSE_ALL_AUTONOMOUS_PAYMENTS",
                title: "PAUSE ALL AUTONOMOUS TRANSACTIONS",
                desc: "Halt all agent autonomous payments across entire cluster immediately.",
              },
              {
                id: "PAUSE_TENANT",
                title: "PAUSE TENANT ACTIONS",
                desc: "Isolate tenant workspace and freeze pending action requests.",
              },
              {
                id: "PAUSE_AGENT",
                title: "PAUSE SPECIFIC AGENT",
                desc: "Temporarily pause execution for designated agent.",
              },
              {
                id: "REVOKE_AGENT",
                title: "REVOKE AGENT CREDENTIALS",
                desc: "Permanently terminate agent credentials and quarantine capability tokens.",
              },
            ].map((opt) => (
              <label
                key={opt.id}
                className={`flex items-start p-3 rounded-lg border cursor-pointer text-xs transition-colors ${
                  selectedAction === opt.id
                    ? "bg-rose-50/50 border-rose-500 ring-1 ring-rose-500"
                    : "border-slate-200 hover:bg-slate-50"
                }`}
              >
                <input
                  type="radio"
                  name="action"
                  className="mt-0.5 text-rose-600 focus:ring-rose-500"
                  checked={selectedAction === opt.id}
                  onChange={() => setSelectedAction(opt.id as EmergencyControlAction)}
                />
                <div className="ml-3">
                  <div className="font-bold text-slate-900">{opt.title}</div>
                  <div className="text-slate-500 text-[11px]">{opt.desc}</div>
                </div>
              </label>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1">
              Operator Principal ID
            </label>
            <input
              type="text"
              required
              className="w-full text-xs font-mono px-3 py-2 border rounded-lg border-slate-300 focus:ring-2 focus:ring-rose-500"
              value={operatorId}
              onChange={(e) => setOperatorId(e.target.value)}
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1">
              Incident / Justification Reason
            </label>
            <input
              type="text"
              required
              className="w-full text-xs px-3 py-2 border rounded-lg border-slate-300 focus:ring-2 focus:ring-rose-500"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-100">
          <Button variant="outline" size="sm" type="button" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" size="sm" type="submit">
            CONFIRM EMERGENCY STOP
          </Button>
        </div>
      </form>
    </Modal>
  );
};
