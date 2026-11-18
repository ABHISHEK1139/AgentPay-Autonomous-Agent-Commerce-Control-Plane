import React, { useState } from "react";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { EmergencyControlAction } from "../../types/emergency";

interface EmergencyControlsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (
    action: EmergencyControlAction,
    operatorId: string,
    reason: string
  ) => void | Promise<void>;
}

export const EmergencyControlsModal: React.FC<EmergencyControlsModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
}) => {
  const [operatorId, setOperatorId] = useState("sec_operator_01");
  const [reason, setReason] = useState("Immediate threat mitigation / anomalous spending containment");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");

  const handleClose = () => {
    if (isSubmitting) return;
    setError("");
    onClose();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setError("");
    try {
      await onConfirm("PAUSE_ALL_AUTONOMOUS_PAYMENTS", operatorId, reason);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title="Emergency Kill Switch Protocol (§21, §67)"
      maxWidth="lg"
      role="alertdialog"
    >
      <form onSubmit={handleSubmit} className="space-y-5">
        <div className="bg-rose-50 border border-rose-200 rounded-xl p-4 text-xs leading-relaxed text-rose-800">
          <p className="font-bold text-rose-900 mb-1">HIGH-IMPACT GOVERNANCE ACTION</p>
          This pauses all autonomous agent transactions. The server must confirm the action before the UI reports success; operator and justification are recorded for audit.
        </div>

        <fieldset>
          <legend className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-2">
            Emergency Control Scope (§21)
          </legend>
          <label className="flex items-start p-3.5 rounded-xl border border-rose-500 bg-rose-50/60 ring-1 ring-rose-500 text-xs">
            <input
              type="radio"
              name="action"
              className="mt-0.5 text-rose-600 focus:ring-rose-500"
              checked
              readOnly
            />
            <div className="ml-3">
              <div className="font-bold text-slate-900">PAUSE ALL AUTONOMOUS TRANSACTIONS</div>
              <div className="text-slate-600 text-[11px] mt-0.5 leading-relaxed">
                Global fail-closed pause. Tenant-specific, agent-specific, and credential-revocation controls are not exposed by this build.
              </div>
            </div>
          </label>
        </fieldset>

        <div className="grid grid-cols-1 gap-4">
          <div>
            <label
              htmlFor="emergency-operator-id"
              className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1.5"
            >
              Operator Principal ID
            </label>
            <input
              id="emergency-operator-id"
              data-autofocus
              type="text"
              required
              disabled={isSubmitting}
              autoComplete="off"
              className="w-full text-sm font-mono px-3.5 py-2.5 border rounded-xl border-slate-300 focus:outline-none focus:ring-2 focus:ring-rose-500 disabled:bg-slate-100"
              value={operatorId}
              onChange={(e) => setOperatorId(e.target.value)}
            />
          </div>

          <div>
            <label
              htmlFor="emergency-reason"
              className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1.5"
            >
              Incident / Justification Reason
            </label>
            <textarea
              id="emergency-reason"
              required
              rows={2}
              disabled={isSubmitting}
              className="w-full text-sm px-3.5 py-2.5 border rounded-xl border-slate-300 focus:outline-none focus:ring-2 focus:ring-rose-500 disabled:bg-slate-100 resize-none"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
        </div>

        {error && (
          <p role="alert" className="text-xs text-rose-700 font-medium bg-rose-50 border border-rose-200 rounded-lg p-3">
            Emergency stop was not confirmed. {error}
          </p>
        )}

        <div className="flex flex-col-reverse sm:flex-row items-stretch sm:items-center justify-end gap-2 pt-4 border-t border-slate-100">
          <Button variant="outline" size="sm" type="button" onClick={handleClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button variant="danger" size="sm" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Confirming emergency stop…" : "CONFIRM EMERGENCY STOP"}
          </Button>
        </div>
      </form>
    </Modal>
  );
};
