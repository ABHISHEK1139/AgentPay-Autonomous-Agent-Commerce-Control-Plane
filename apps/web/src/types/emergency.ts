export type EmergencyControlAction =
  | "PAUSE_ALL_AUTONOMOUS_PAYMENTS"
  | "PAUSE_AGENT"
  | "REVOKE_AGENT"
  | "PAUSE_TENANT";

export interface EmergencyState {
  isPaused: boolean;
  pausedAt?: string;
  pausedBy?: string;
  reason?: string;
  scope: "GLOBAL" | "TENANT" | "AGENT";
  targetId?: string;
}

export interface EmergencyAuditRecord {
  id: string;
  timestamp: string;
  operatorId: string;
  action: EmergencyControlAction;
  scope: string;
  targetId?: string;
  reason: string;
  ipAddress: string;
}
