import React from "react";
import { Badge, BadgeVariant } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { MOCK_SECURITY_INCIDENTS } from "../../services/mockData";

export const SecurityArea: React.FC = () => {
  const getSeverityBadge = (sev: string): { variant: BadgeVariant; label: string } => {
    switch (sev) {
      case "CRITICAL":
        return { variant: "danger", label: "CRITICAL THREAT" };
      case "HIGH":
        return { variant: "danger", label: "HIGH SEVERITY" };
      case "MEDIUM":
        return { variant: "warning", label: "MEDIUM SEVERITY" };
      default:
        return { variant: "info", label: "LOW SEVERITY" };
    }
  };

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card title="Security Alerts (24h)" subtitle="Threat detection triggers">
          <div className="text-3xl font-black text-rose-600 mt-1 font-mono">
            {MOCK_SECURITY_INCIDENTS.length}
          </div>
          <span className="text-xs text-rose-600 font-semibold mt-2 block">
            100% intercepted fail-closed (§2.4)
          </span>
        </Card>

        <Card title="Quarantined Agents" subtitle="Privilege escalation suspects">
          <div className="text-3xl font-black text-amber-600 mt-1 font-mono">1</div>
          <span className="text-xs text-slate-500 mt-2 block">ag_compromised_07 isolated</span>
        </Card>

        <Card title="Audit Ledger Status" subtitle="Cryptographic Hash Chain">
          <div className="text-base font-bold text-emerald-600 mt-1 flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-500"></span> CHAIN INTEGRITY VERIFIED
          </div>
          <span className="text-xs text-slate-500 mt-2 block font-mono">
            Genesis to block #1,492 tamper-free
          </span>
        </Card>
      </div>

      <Card
        title="Security Incidents & Tampering Interceptions (§52, §53, §63, §110)"
        subtitle="Prompt injection, price manipulation, and inventory deception event log"
      >
        <div className="space-y-3">
          {MOCK_SECURITY_INCIDENTS.map((inc) => {
            const badge = getSeverityBadge(inc.severity);
            return (
              <div
                key={inc.id}
                className="p-4 rounded-xl border border-slate-200 bg-slate-50/50 flex flex-wrap items-center justify-between gap-3 hover:bg-slate-50"
              >
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-bold text-slate-900">{inc.id}</span>
                    <Badge variant={badge.variant}>{badge.label}</Badge>
                    <span className="font-mono text-[10px] text-slate-400">
                      {new Date(inc.timestamp).toLocaleTimeString()} • Agent: {inc.agentId}
                    </span>
                  </div>
                  <p className="text-xs font-semibold text-slate-900">{inc.description}</p>
                  <p className="text-[11px] text-slate-500 font-mono">
                    Mitigation: <strong className="text-slate-700">{inc.actionTaken}</strong>
                  </p>
                </div>

                <Badge variant={inc.resolved ? "success" : "danger"}>
                  {inc.resolved ? "CONTAINED" : "ACTIVE INVESTIGATION"}
                </Badge>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
};
