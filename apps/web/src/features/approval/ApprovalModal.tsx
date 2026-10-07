import React, { useState } from "react";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { ApprovalItem } from "../../types/approval";
import { ApprovalContextView } from "./ApprovalContextView";

interface ApprovalModalProps {
  item: ApprovalItem | null;
  isOpen: boolean;
  onClose: () => void;
  onApprove: (id: string, reason: string) => void;
  onReject: (id: string, reason: string) => void;
}

export const ApprovalModal: React.FC<ApprovalModalProps> = ({
  item,
  isOpen,
  onClose,
  onApprove,
  onReject,
}) => {
  const [reason, setReason] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (!item) return null;

  const handleApprove = async () => {
    setIsSubmitting(true);
    await onApprove(item.id, reason || "Approved by Human Operator");
    setIsSubmitting(false);
    onClose();
  };

  const handleReject = async () => {
    setIsSubmitting(true);
    await onReject(item.id, reason || "Rejected by Human Operator");
    setIsSubmitting(false);
    onClose();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Human Authorization Review Screen (§66)"
      maxWidth="4xl"
    >
      <div className="space-y-5">
        <ApprovalContextView item={item} />

        <div className="border-t border-slate-200 pt-4 bg-slate-50 -mx-6 -mb-6 p-6 rounded-b-2xl">
          <label className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1.5">
            Approver Decision Note / Justification (Audit Recorded)
          </label>
          <input
            type="text"
            className="w-full text-xs px-3.5 py-2.5 rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white"
            placeholder="e.g. Verified developer monitor necessity and department budget allocation."
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />

          <div className="flex items-center justify-between gap-3 mt-4 pt-3 border-t border-slate-200">
            <div className="text-[11px] text-slate-500 font-mono">
              Binding locked to Action {item.actionId} v{item.actionVersion}. Re-use strictly prevented.
            </div>

            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={onClose} disabled={isSubmitting}>
                Dismiss
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={handleReject}
                disabled={isSubmitting || item.status !== "PENDING"}
              >
                Reject Action
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={handleApprove}
                disabled={isSubmitting || item.status !== "PENDING"}
              >
                Sign & Authorize Execution
              </Button>
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
};
