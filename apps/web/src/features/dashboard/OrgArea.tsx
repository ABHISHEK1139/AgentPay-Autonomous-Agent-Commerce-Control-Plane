import React from "react";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { MOCK_ORG_DEPARTMENTS } from "../../services/mockData";

export const OrgArea: React.FC = () => {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card title="Total Department Allocation" subtitle="Fiscal Quarter Q4 Budget">
          <div className="text-3xl font-black text-slate-900 mt-1 font-mono">
            ₹10,00,000.00
          </div>
          <span className="text-xs text-slate-500 mt-2 block">Across 3 business departments</span>
        </Card>

        <Card title="Current Quarter Burn" subtitle="Autonomous + Supervised Spend">
          <div className="text-3xl font-black text-blue-600 mt-1 font-mono">
            ₹3,05,000.00
          </div>
          <span className="text-xs text-slate-500 mt-2 block">30.5% budget utilization</span>
        </Card>

        <Card title="Active Spending Policies" subtitle="Deterministic Engine Rules">
          <div className="text-3xl font-black text-emerald-600 mt-1 font-mono">5</div>
          <span className="text-xs text-slate-500 mt-2 block">0 policy violations detected today</span>
        </Card>
      </div>

      <Card title="Department Budgets & Governance Chains (§18, §19, §49)" subtitle="Hierarchical multi-tenant limits and spending authorities">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500 uppercase tracking-wider font-semibold border-b border-slate-200">
              <tr>
                <th className="px-4 py-3">Department</th>
                <th className="px-4 py-3">Allocated Budget</th>
                <th className="px-4 py-3">Spent to Date</th>
                <th className="px-4 py-3">Utilization</th>
                <th className="px-4 py-3">Agent Count</th>
                <th className="px-4 py-3">Active Policies</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {MOCK_ORG_DEPARTMENTS.map((dept) => {
                const util = Math.round((dept.budgetSpentMinor / dept.budgetAllocatedMinor) * 100);
                return (
                  <tr key={dept.id} className="hover:bg-slate-50/50">
                    <td className="px-4 py-3 font-bold text-slate-900">{dept.name}</td>
                    <td className="px-4 py-3 font-mono font-medium">
                      ₹{(dept.budgetAllocatedMinor / 100).toLocaleString("en-IN")}
                    </td>
                    <td className="px-4 py-3 font-mono font-medium">
                      ₹{(dept.budgetSpentMinor / 100).toLocaleString("en-IN")}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="w-24 bg-slate-200 h-2 rounded-full overflow-hidden">
                          <div
                            className="bg-blue-600 h-full rounded-full"
                            style={{ width: `${util}%` }}
                          ></div>
                        </div>
                        <span className="font-mono text-slate-600 font-semibold">{util}%</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 font-mono">{dept.agentCount}</td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1">
                        {dept.activePolicies.map((p) => (
                          <Badge key={p} variant="neutral">
                            {p}
                          </Badge>
                        ))}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};
