import React from "react";
import { Badge } from "../../components/ui/Badge";
import { Card } from "../../components/ui/Card";
import { MOCK_MERCHANT_PRODUCTS } from "../../services/mockData";

export const MerchantArea: React.FC = () => {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card title="Catalog SKUs" subtitle="Available to Autonomous Agents">
          <div className="text-3xl font-black text-slate-900 mt-1 font-mono">
            {MOCK_MERCHANT_PRODUCTS.length}
          </div>
          <span className="text-xs text-slate-500 mt-2 block">
            {MOCK_MERCHANT_PRODUCTS.filter((p) => p.allowAgentPurchase).length} enabled for agent checkout
          </span>
        </Card>

        <Card title="Reserved Inventory" subtitle="Pending order fulfillment">
          <div className="text-3xl font-black text-blue-600 mt-1 font-mono">
            {MOCK_MERCHANT_PRODUCTS.reduce((acc, p) => acc + p.inventoryReserved, 0)} units
          </div>
          <span className="text-xs text-slate-500 mt-2 block">
            Saga reservation locks active (§44)
          </span>
        </Card>

        <Card title="Agent Protocol Support" subtitle="Supported Inbound Formats">
          <div className="text-sm font-bold text-slate-800 mt-2 flex flex-wrap gap-1.5">
            <Badge variant="info">ACP 1.0</Badge>
            <Badge variant="info">AP2 1.0</Badge>
            <Badge variant="info">UAP 1.0</Badge>
            <Badge variant="info">x402</Badge>
          </div>
          <span className="text-xs text-slate-500 mt-2 block">All mapped to canonical ActionRequest</span>
        </Card>
      </div>

      <Card title="Merchant Product Catalog & Live Stock (§30, §37)" subtitle="Real-time stock reservation and agent purchasing whitelist">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-slate-50 text-slate-500 uppercase tracking-wider font-semibold border-b border-slate-200">
              <tr>
                <th className="px-4 py-3">Product Title & SKU</th>
                <th className="px-4 py-3">Category</th>
                <th className="px-4 py-3">Unit Price</th>
                <th className="px-4 py-3">Available Stock</th>
                <th className="px-4 py-3">Reserved</th>
                <th className="px-4 py-3">Agent Checkout</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {MOCK_MERCHANT_PRODUCTS.map((prod) => (
                <tr key={prod.id} className="hover:bg-slate-50/50">
                  <td className="px-4 py-3">
                    <div className="font-bold text-slate-900">{prod.title}</div>
                    <div className="font-mono text-slate-400 text-[11px]">{prod.sku}</div>
                  </td>
                  <td className="px-4 py-3 capitalize text-slate-600">{prod.category}</td>
                  <td className="px-4 py-3 font-mono font-bold text-slate-900">
                    ₹{(prod.priceMinor / 100).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
                  </td>
                  <td className="px-4 py-3 font-mono">
                    <span className={prod.inventoryAvailable === 0 ? "text-rose-600 font-bold" : "text-emerald-700 font-bold"}>
                      {prod.inventoryAvailable}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-slate-500">{prod.inventoryReserved}</td>
                  <td className="px-4 py-3">
                    <Badge variant={prod.allowAgentPurchase ? "success" : "neutral"}>
                      {prod.allowAgentPurchase ? "ALLOWED" : "HUMAN ONLY"}
                    </Badge>
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
