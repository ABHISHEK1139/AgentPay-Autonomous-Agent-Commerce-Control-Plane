import React from "react";
import { Badge, BadgeVariant } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { ApprovalItem } from "../../types/approval";

interface ApprovalListProps {
  approvals: ApprovalItem[];
  onSelect: (item: ApprovalItem) => void;
}

export const ApprovalList: React.FC<ApprovalListProps> = ({ approvals, onSelect }) => {
  const getStatusBadge = (status: ApprovalItem["status"]): { variant: BadgeVariant; label: string } => {
    switch (status) {
      case "PENDING":
        return { variant: "warning", label: "PENDING REVIEW" };
      case "APPROVED":
        return { variant: "success", label: "AUTHORIZED" };
      case "REJECTED":
        return { variant: "danger", label: "REJECTED" };
      default:
        return { variant: "neutral", label: status };
    }
  };

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
        <div>
          <h2 className="text-base font-bold text-slate-900">Human Approval Review Queue (§66)</h2>
          <p className="text-xs text-slate-500 mt-0.5">
            Cryptographically bound pending authorizations requiring human oversight.
          </p>
        </div>
        <span className="text-xs font-mono bg-amber-50 text-amber-700 border border-amber-200 px-2.5 py-1 rounded-full font-semibold">
          {approvals.filter((a) => a.status === "PENDING").length} Pending Requests
        </span>
      </div>

      <div className="divide-y divide-slate-100">
        {approvals.length === 0 ? (
          <div className="p-8 text-center text-xs text-slate-500">No approval requests currently queued.</div>
        ) : (
          approvals.map((item) => {
            const badge = getStatusBadge(item.status);
            return (
              <div
                key={item.id}
                className="p-4 hover:bg-slate-50 transition-colors flex flex-wrap items-center justify-between gap-4"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-bold text-slate-800">{item.id}</span>
                    <Badge variant={badge.variant}>{badge.label}</Badge>
                    <span className="text-[11px] text-slate-400 font-mono">
                      v{item.actionVersion} • {new Date(item.createdAt).toLocaleTimeString()}
                    </span>
                  </div>

                  <h4 className="text-sm font-semibold text-slate-900">
                    {item.what.actionDescription} — <span className="text-blue-600">{item.product.title}</span>
                  </h4>

                  <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                    <span>
                      Agent: <strong className="text-slate-700">{item.who.agentName}</strong>
                    </span>
                    <span>•</span>
                    <span>
                      Principal: <strong className="text-slate-700">{item.who.principalName}</strong>
                    </span>
                    <span>•</span>
                    <span>
                      Merchant: <strong className="text-slate-700">{item.merchant.name}</strong>
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-4">
                  <div className="text-right">
                    <div className="text-base font-mono font-bold text-slate-900">{item.howMuch.formatted}</div>
                    <div className="text-[10px] text-slate-500 font-mono">Risk: {item.risk.level} ({item.risk.score})</div>
                  </div>

                  <Button
                    variant={item.status === "PENDING" ? "primary" : "outline"}
                    size="sm"
                    onClick={() => onSelect(item)}
                  >
                    {item.status === "PENDING" ? "Inspect 11 Fields" : "View Record"}
                  </Button>
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
