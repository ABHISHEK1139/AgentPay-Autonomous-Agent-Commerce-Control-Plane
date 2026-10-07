import React from "react";
import { Badge } from "../../components/ui/Badge";
import { ApprovalItem } from "../../types/approval";

interface ApprovalContextViewProps {
  item: ApprovalItem;
}

export const ApprovalContextView: React.FC<ApprovalContextViewProps> = ({ item }) => {
  return (
    <div className="space-y-4">
      {/* Header Banner */}
      <div className="bg-slate-900 text-white rounded-xl p-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <span className="text-[10px] font-mono uppercase tracking-wider text-slate-400">
            Approval Request Binding ID: {item.id}
          </span>
          <h3 className="text-lg font-bold text-white mt-0.5">{item.what.actionDescription}</h3>
        </div>
        <div className="text-right">
          <div className="text-xs text-slate-400">Total Requested Amount</div>
          <div className="text-2xl font-black text-amber-400 font-mono">{item.howMuch.formatted}</div>
        </div>
      </div>

      {/* The 11 Mandatory Decision Context Fields (§66) Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
        {/* 1. WHO */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
          <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-blue-500"></span> 1. WHO (Principal & Agent)
          </div>
          <div className="space-y-1 text-slate-800">
            <div><span className="text-slate-500">Agent:</span> <span className="font-mono font-semibold">{item.who.agentName}</span> ({item.who.agentId})</div>
            <div><span className="text-slate-500">Principal:</span> <span className="font-semibold">{item.who.principalName}</span></div>
            <div><span className="text-slate-500">Org:</span> {item.who.organization}</div>
          </div>
        </div>

        {/* 2. WHAT */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
          <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-indigo-500"></span> 2. WHAT (Action & Domain)
          </div>
          <div className="space-y-1 text-slate-800">
            <div><span className="text-slate-500">Type:</span> <span className="font-mono font-semibold">{item.what.actionType}</span></div>
            <div><span className="text-slate-500">Domain:</span> <span className="font-semibold uppercase">{item.what.domain}</span></div>
            <div><span className="text-slate-500">Desc:</span> {item.what.actionDescription}</div>
          </div>
        </div>

        {/* 3. WHY */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
          <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-cyan-500"></span> 3. WHY (Intent & Budget Tag)
          </div>
          <div className="space-y-1 text-slate-800">
            <div><span className="text-slate-500">Purpose:</span> {item.why.intentPurpose}</div>
            <div><span className="text-slate-500">Justification:</span> {item.why.justification}</div>
            <div><span className="text-slate-500">Budget:</span> <span className="font-mono bg-slate-200 px-1 py-0.5 rounded">{item.why.budgetTag}</span></div>
          </div>
        </div>

        {/* 4. HOW MUCH */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
          <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-amber-500"></span> 4. HOW MUCH (Amount & Settlement)
          </div>
          <div className="space-y-1 text-slate-800">
            <div><span className="text-slate-500">Amount:</span> <span className="font-bold font-mono text-sm text-slate-900">{item.howMuch.formatted}</span> ({item.howMuch.currency})</div>
            <div><span className="text-slate-500">Minor Units:</span> <span className="font-mono">{item.howMuch.amountMinor}</span></div>
            {item.howMuch.exchangeRateNotice && <div className="text-[11px] text-slate-500">{item.howMuch.exchangeRateNotice}</div>}
          </div>
        </div>

        {/* 5. MERCHANT */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
          <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-500"></span> 5. MERCHANT (Verification & Trust)
          </div>
          <div className="space-y-1 text-slate-800">
            <div><span className="text-slate-500">Name:</span> <span className="font-semibold">{item.merchant.name}</span></div>
            <div><span className="text-slate-500">Status:</span> {item.merchant.isVerified ? <Badge variant="success">Verified Merchant</Badge> : <Badge variant="warning">Unverified</Badge>}</div>
            <div><span className="text-slate-500">Trust Score:</span> <span className="font-mono font-bold text-emerald-700">{item.merchant.trustScore}/100</span></div>
          </div>
        </div>

        {/* 6. PRODUCT */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
          <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-teal-500"></span> 6. PRODUCT (SKU & Spec)
          </div>
          <div className="space-y-1 text-slate-800">
            <div><span className="text-slate-500">Item:</span> <span className="font-semibold">{item.product.title}</span></div>
            <div><span className="text-slate-500">SKU:</span> <span className="font-mono text-slate-600">{item.product.sku}</span></div>
            <div><span className="text-slate-500">Category:</span> <span className="capitalize">{item.product.category}</span></div>
          </div>
        </div>

        {/* 7. EVIDENCE */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
          <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-sky-500"></span> 7. EVIDENCE (Price & Inventory Provenance)
          </div>
          <div className="space-y-1 text-slate-800">
            <div><span className="text-slate-500">Price Source:</span> {item.evidence.priceSource}</div>
            <div><span className="text-slate-500">Observed Stock:</span> <span className="font-mono font-bold">{item.evidence.inventorySnapshot} units available</span></div>
            <div><span className="text-slate-500">SHA-256 Hash:</span> <span className="font-mono text-[10px] text-slate-500 truncate block">{item.evidence.hashSha256}</span></div>
          </div>
        </div>

        {/* 8. POLICY */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
          <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-violet-500"></span> 8. POLICY (Deterministic Rule Trigger)
          </div>
          <div className="space-y-1 text-slate-800">
            <div><span className="text-slate-500">Rule:</span> <span className="font-semibold text-rose-700">{item.policy.triggeredRule}</span></div>
            <div><span className="text-slate-500">Autonomous Limit:</span> <span className="font-mono">₹{(item.policy.singleActionLimitMinor / 100).toLocaleString("en-IN")}</span></div>
            <div><span className="text-slate-500">Daily Spent:</span> <span className="font-mono">₹{(item.policy.currentDailySpentMinor / 100).toLocaleString("en-IN")}</span></div>
          </div>
        </div>

        {/* 9. RISK */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
          <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-rose-500"></span> 9. RISK (Assessment & Score)
          </div>
          <div className="space-y-1 text-slate-800">
            <div className="flex items-center gap-2">
              <span className="text-slate-500">Level:</span>
              <Badge variant={item.risk.level === "LOW" ? "success" : item.risk.level === "MEDIUM" ? "warning" : "danger"}>
                {item.risk.level} ({item.risk.score})
              </Badge>
            </div>
            <div>
              <span className="text-slate-500">Risk Factors:</span>
              <ul className="list-disc list-inside mt-0.5 text-[11px] text-slate-600">
                {item.risk.factors.map((f, i) => (
                  <li key={i}>{f.description} (w: {f.weight})</li>
                ))}
              </ul>
            </div>
          </div>
        </div>

        {/* 10. RECOMMENDATION */}
        <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
          <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-purple-500"></span> 10. RECOMMENDATION (JEV Intelligence)
          </div>
          <div className="space-y-1 text-slate-800">
            <div><span className="text-slate-500">Engine:</span> <span className="font-semibold">{item.recommendation.advisor}</span> (conf: {item.recommendation.confidence})</div>
            <div><span className="text-slate-500">Advisory:</span> <Badge variant="warning">{item.recommendation.action}</Badge></div>
            <div className="text-[11px] text-slate-600 mt-1 italic">"{item.recommendation.rationale}"</div>
          </div>
        </div>
      </div>

      {/* 11. CONFLICTS */}
      <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
        <div className="font-bold text-slate-700 uppercase tracking-wide text-[10px] mb-1 flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-red-600"></span> 11. CONFLICTS (Evidence Discrepancy Checks)
        </div>
        {item.conflicts.length === 0 ? (
          <div className="text-xs text-emerald-700 flex items-center gap-1.5 font-medium">
            <span>✓</span> 0 conflicting evidence signals detected between agent assertion and merchant sources.
          </div>
        ) : (
          <div className="space-y-2 mt-1">
            {item.conflicts.map((c, i) => (
              <div key={i} className="bg-rose-50 border border-rose-200 rounded p-2 text-xs text-rose-900">
                <span className="font-bold">{c.type}:</span> {c.description} (Asserted: '{c.asserted}' vs Verified: '{c.verified}')
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
