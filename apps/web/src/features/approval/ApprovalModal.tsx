import React, { useEffect, useState } from "react";
import { Button } from "../../components/ui/Button";
import { Modal } from "../../components/ui/Modal";
import { ApprovalItem } from "../../types/approval";
import { ApprovalContextView } from "./ApprovalContextView";

interface ApprovalModalProps {
  item: ApprovalItem | null;
  isOpen: boolean;
  onClose: () => void;
  onApprove: (id: string, reason: string) => void | Promise<void>;
  onReject: (id: string, reason: string) => void | Promise<void>;
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
  const [error, setError] = useState("");

  useEffect(() => {
    if (isOpen) {
      setReason("");
      setError("");
    }
  }, [isOpen, item?.id]);

  if (!item) return null;

  const handleClose = () => {
    if (isSubmitting) return;
    setError("");
    onClose();
  };

  const handleApprove = async () => {
    setIsSubmitting(true);
    setError("");
    try {
      await onApprove(item.id, reason || "Approved by Human Operator");
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleReject = async () => {
    setIsSubmitting(true);
    setError("");
    try {
      await onReject(item.id, reason || "Rejected by Human Operator");
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
      title="Human Authorization Review Screen (§66)"
      maxWidth="4xl"
    >
      <div className="space-y-5">
        <ApprovalContextView item={item} />

        <div className="border-t border-slate-200 pt-4 bg-slate-50 -mx-6 -mb-6 p-6 rounded-b-2xl">
          <label
            htmlFor="approval-decision-note"
            className="block text-xs font-semibold text-slate-700 uppercase tracking-wide mb-1.5"
          >
            Approver Decision Note / Justification (Audit Recorded)
          </label>
          <input
            id="approval-decision-note"
            data-autofocus
            type="text"
            disabled={isSubmitting}
            className="w-full text-sm px-3.5 py-2.5 rounded-xl border border-slate-300 focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white disabled:bg-slate-100"
            placeholder="e.g. Verified developer monitor necessity and department budget allocation."
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />

          {error && (
            <p role="alert" className="text-xs text-red-600 font-medium mt-3">
              {error}
            </p>
          )}

          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 mt-4 pt-3 border-t border-slate-200">
            <div className="text-[11px] text-slate-500 font-mono leading-relaxed">
              Binding locked to Action {item.actionId} v{item.actionVersion}. Re-use strictly prevented.
            </div>

            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={handleClose} disabled={isSubmitting}>
                Dismiss
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={handleReject}
                disabled={isSubmitting || item.status !== "PENDING"}
              >
                {isSubmitting ? "Submitting…" : "Reject Action"}
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={handleApprove}
                disabled={isSubmitting || item.status !== "PENDING"}
              >
                {isSubmitting ? "Authorizing…" : "Sign & Authorize Execution"}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
};
