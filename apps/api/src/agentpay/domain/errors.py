from __future__ import annotations

from dataclasses import dataclass


class DomainError(Exception):
    """Base class for domain errors. Carries a stable machine-readable code."""

    code = "DOMAIN_ERROR"


class InvalidMoneyError(DomainError):
    code = "INVALID_MONEY"


class NonIntegerMoneyError(InvalidMoneyError):
    code = "NON_INTEGER_MONEY"


class InvalidCurrencyError(InvalidMoneyError):
    code = "INVALID_CURRENCY"


class NegativeMoneyError(InvalidMoneyError):
    code = "NEGATIVE_MONEY"


class CurrencyMismatchError(DomainError):
    code = "CURRENCY_MISMATCH"


class InvalidActionRequestError(DomainError):
    code = "INVALID_ACTION_REQUEST"


class InvalidPolicyInputError(DomainError):
    code = "INVALID_POLICY_INPUT"


@dataclass(frozen=True, slots=True)
class PolicyViolation:
    """A structured, auditable description of why a domain invariant rejected input."""

    code: str
    message: str


class IdentityError(DomainError):
    """Base class for identity, tenancy, and authorization errors."""

    code = "IDENTITY_ERROR"


class InvalidIdentityError(IdentityError):
    """Raised when an identity model (Tenant, User, Agent) fails validation."""

    code = "INVALID_IDENTITY"


class CrossTenantAccessError(IdentityError):
    """Raised when an operation attempts to access resources across tenant boundaries."""

    code = "CROSS_TENANT_ACCESS_DENIED"


class AgentNotActiveError(IdentityError):
    """Raised when an action is attempted by an agent that is not in ACTIVE status."""

    code = "AGENT_NOT_ACTIVE"


class AgentSuspendedError(AgentNotActiveError):
    """Raised when an action is attempted by a suspended agent."""

    code = "AGENT_SUSPENDED"


class AgentRevokedError(AgentNotActiveError, InvalidIdentityError):
    """Raised when an action is attempted by a revoked agent (terminal state)."""

    code = "AGENT_REVOKED"


class AgentExpiredError(AgentNotActiveError):
    """Raised when an action is attempted after an agent credential/token has expired."""

    code = "AGENT_EXPIRED"


class AgentQuarantinedError(AgentNotActiveError):
    """Raised when an action is attempted by a quarantined agent."""

    code = "AGENT_QUARANTINED"


class PrivilegeEscalationAttemptError(IdentityError):
    """Raised when an agent attempts to widen its own limits, capabilities, or permissions."""

    code = "PRIVILEGE_ESCALATION_ATTEMPT"


class InvalidAgentStatusTransitionError(InvalidIdentityError):
    """Raised when an agent status transition violates the lifecycle state machine."""

    code = "INVALID_AGENT_STATUS_TRANSITION"


class MissingCapabilityError(IdentityError):
    """Raised when an agent lacks the capability required to perform an action."""

    code = "MISSING_CAPABILITY"


# Authentication & Token Errors (§14)
class AuthenticationError(IdentityError):
    """Raised when a caller cannot be authenticated (§14)."""

    code = "AUTHENTICATION_FAILED"


class MissingTokenError(AuthenticationError):
    code = "MISSING_TOKEN"


class InvalidTokenError(AuthenticationError):
    code = "INVALID_TOKEN"


class TokenExpiredError(AuthenticationError):
    code = "TOKEN_EXPIRED"


class TokenRevokedError(AuthenticationError):
    code = "TOKEN_REVOKED"


class TokenAudienceError(AuthenticationError):
    code = "TOKEN_AUDIENCE_INVALID"


class TokenLifetimeTooLongError(AuthenticationError):
    """Raised when a requested token lifetime exceeds the policy maximum (§14)."""

    code = "TOKEN_LIFETIME_TOO_LONG"


class AuthorizationError(IdentityError):
    """Raised when an authenticated principal lacks authority for a request (§16)."""

    code = "AUTHORIZATION_DENIED"


class MissingScopeError(AuthorizationError):
    code = "MISSING_SCOPE"


class AgentIdentityMismatchError(AuthorizationError):
    """Raised when an agent token tries to act as a different agent (§15)."""

    code = "AGENT_IDENTITY_MISMATCH"


class RateLimitExceededError(DomainError):
    """Raised when a caller exceeds its configured request budget (§62)."""

    code = "RATE_LIMIT_EXCEEDED"

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after_seconds: float = 0.0,
        scope: str = "agent",
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds
        self.scope = scope


# Tool Registry Errors (§26, §27)
class ToolRegistryError(DomainError):
    code = "TOOL_REGISTRY_ERROR"


class UnknownToolError(ToolRegistryError):
    """Raised when a caller invokes a tool that is not in the fixed registry (§26)."""

    code = "UNKNOWN_TOOL"


class ToolPermissionDeniedError(ToolRegistryError):
    """Raised when a tool is invoked without its required permission (§26)."""

    code = "TOOL_PERMISSION_DENIED"


class ToolCallerNotAllowedError(ToolRegistryError):
    """Raised when a caller type is not permitted to invoke a tool (§26)."""

    code = "TOOL_CALLER_NOT_ALLOWED"


# Commerce Errors
class InvalidCommerceError(DomainError):
    code = "INVALID_COMMERCE"


class InsufficientInventoryError(InvalidCommerceError):
    code = "INSUFFICIENT_INVENTORY"


class InvalidEvidenceError(DomainError):
    code = "INVALID_EVIDENCE"


class EvidenceConflictError(DomainError):
    code = "EVIDENCE_CONFLICT"


# Approval & Decision Errors
class InvalidApprovalError(DomainError):
    code = "INVALID_APPROVAL"


class ApprovalBindingMismatchError(InvalidApprovalError):
    code = "APPROVAL_BINDING_MISMATCH"


class InvalidApprovalTransitionError(InvalidApprovalError):
    code = "INVALID_APPROVAL_TRANSITION"


class InvalidDecisionError(DomainError):
    code = "INVALID_DECISION"


# Payment Errors
class InvalidPaymentError(DomainError):
    code = "INVALID_PAYMENT"


class PaymentTimeoutError(InvalidPaymentError):
    code = "PAYMENT_TIMEOUT"


class PaymentDeclinedError(InvalidPaymentError):
    code = "PAYMENT_DECLINED"


class IdempotencyConflictError(DomainError):
    code = "IDEMPOTENCY_CONFLICT"


class SagaCompensationError(DomainError):
    code = "SAGA_COMPENSATION_ERROR"


# Audit Errors
class InvalidAuditEventError(DomainError):
    code = "INVALID_AUDIT_EVENT"


class AuditChainIntegrityError(DomainError):
    code = "AUDIT_CHAIN_INTEGRITY_ERROR"


# Safety & Loop Protection Errors (§64, §65)
class SafetyError(DomainError):
    code = "SAFETY_ERROR"


class LoopBudgetExceededError(SafetyError):
    code = "LOOP_BUDGET_EXCEEDED"


class FinancialSafetyBudgetExceededError(SafetyError):
    code = "FINANCIAL_SAFETY_BUDGET_EXCEEDED"


# Additional Credential Errors (§14, §15, §67)
class InvalidCredentialError(AuthenticationError):
    code = "INVALID_CREDENTIAL"


class CredentialExpiredError(AuthenticationError):
    code = "CREDENTIAL_EXPIRED"


class CredentialRevokedError(AuthenticationError):
    code = "CREDENTIAL_REVOKED"


class UnauthorizedOperatorError(AuthenticationError):
    code = "UNAUTHORIZED_OPERATOR"


class UnauthorizedToolAccessError(ToolRegistryError):
    code = "UNAUTHORIZED_TOOL_ACCESS"
