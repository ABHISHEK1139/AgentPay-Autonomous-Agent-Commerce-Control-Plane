export interface BuyerAgent {
  id: string;
  name: string;
  status: "ACTIVE" | "PAUSED" | "REVOKED";
  singleLimitMinor: number;
  dailyLimitMinor: number;
  dailySpentMinor: number;
  currency: string;
  categories: string[];
}

export interface MerchantProduct {
  id: string;
  title: string;
  sku: string;
  category: string;
  priceMinor: number;
  currency: string;
  inventoryAvailable: number;
  inventoryReserved: number;
  allowAgentPurchase: boolean;
}

export interface OrgDepartment {
  id: string;
  name: string;
  budgetAllocatedMinor: number;
  budgetSpentMinor: number;
  currency: string;
  agentCount: number;
  activePolicies: string[];
}

export interface SecurityIncident {
  id: string;
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  type: "PRICE_MANIPULATION" | "INVENTORY_EXHAUSTION" | "PRIVILEGE_ESCALATION" | "LOOP_DETECTED" | "ANOMALY";
  description: string;
  agentId: string;
  timestamp: string;
  resolved: boolean;
  actionTaken: string;
}
