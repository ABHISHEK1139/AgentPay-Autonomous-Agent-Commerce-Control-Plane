import React from "react";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { MOCK_BUYER_AGENTS } from "../../services/mockData";

export const BuyerArea: React.FC = () => {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card title="Active Buyer Agents" subtitle="Delegated autonomous shoppers">
          <div className="text-3xl font-black text-slate-900 mt-1 font-mono">
            {MOCK_BUYER_AGENTS.filter((a) => a.status === "ACTIVE").length}
          </div>
          <span className="text-xs text-slate-500 mt-2 block">
            Across 3 certified department domains
          </span>
        </Card>

        <Card title="Autonomous Spend (24h)" subtitle="Aggregated daily expenditure">
          <div className="text-3xl font-black text-emerald-600 mt-1 font-mono">
            ₹1,749.00
          </div>
          <span className="text-xs text-slate-500 mt-2 block">
            Within daily cap limit of ₹10,000.00
          </span>
        </Card>

        <Card title="Approval Queue" subtitle="Pending human reviews">
          <div className="text-3xl font-black text-amber-500 mt-1 font-mono">2</div>
          <span className="text-xs text-slate-500 mt-2 block">
            Avg review resolution time: 3.2m
          </span>
        </Card>
      </div>

      <Card title="Registered Buyer Agents & Limits (§15, §16)" subtitle="Autonomous scope and spending parameters">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500 uppercase tracking-wider font-semibold border-b border-slate-200">
              <tr>
                <th className="px-4 py-3">Agent ID & Name</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Single Limit</th>
                <th className="px-4 py-3">Daily Limit</th>
                <th className="px-4 py-3">Spent Today</th>
                <th className="px-4 py-3">Allowed Categories</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {MOCK_BUYER_AGENTS.map((agent) => (
                <tr key={agent.id} className="hover:bg-slate-50/50">
                  <td className="px-4 py-3">
                    <div className="font-bold text-slate-900">{agent.name}</div>
                    <div className="font-mono text-slate-400 text-[11px]">{agent.id}</div>
                  </td>
                  <td className="px-4 py-3">
                    <Badge variant={agent.status === "ACTIVE" ? "success" : "warning"}>
                      {agent.status}
                    </Badge>
                  </td>
                  <td className="px-4 py-3 font-mono font-medium">
                    ₹{(agent.singleLimitMinor / 100).toLocaleString("en-IN")}
                  </td>
                  <td className="px-4 py-3 font-mono font-medium">
                    ₹{(agent.dailyLimitMinor / 100).toLocaleString("en-IN")}
                  </td>
                  <td className="px-4 py-3 font-mono text-slate-600">
                    ₹{(agent.dailySpentMinor / 100).toLocaleString("en-IN")}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {agent.categories.map((c) => (
                        <span key={c} className="bg-slate-100 px-2 py-0.5 rounded text-[10px] font-mono text-slate-700">
                          {c}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
};
