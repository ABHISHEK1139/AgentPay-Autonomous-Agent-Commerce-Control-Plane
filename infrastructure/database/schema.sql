-- ============================================================================
-- AgentPay Autonomous Agent Commerce Control Plane
-- Relational Database Foundation (§49, §50, §51)
-- PostgreSQL DDL Schema
-- ============================================================================

-- 1. Tenants Table (§49, §50)
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_tenants_status ON tenants(status);

-- 2. Agents Table (§15, §49)
CREATE TABLE IF NOT EXISTS agents (
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    agent_id VARCHAR(64) NOT NULL,
    principal_id VARCHAR(64) NOT NULL,
    issuer VARCHAR(255) NOT NULL,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (tenant_id, agent_id)
);

CREATE INDEX IF NOT EXISTS ix_agents_tenant_status ON agents(tenant_id, status);
CREATE INDEX IF NOT EXISTS ix_agents_tenant_principal ON agents(tenant_id, principal_id);

-- 3. Credentials Table (§14, §49)
CREATE TABLE IF NOT EXISTS credentials (
    credential_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    principal_id VARCHAR(64) NOT NULL,
    credential_type VARCHAR(32) NOT NULL,
    secret_hash VARCHAR(128) NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    role VARCHAR(32),
    scopes JSONB NOT NULL DEFAULT '[]'::jsonb,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_credentials_tenant_principal ON credentials(tenant_id, principal_id);
CREATE INDEX IF NOT EXISTS ix_credentials_secret_hash ON credentials(secret_hash);
CREATE INDEX IF NOT EXISTS ix_credentials_tenant_status ON credentials(tenant_id, status);

-- 4. Products Table (§30, §49)
CREATE TABLE IF NOT EXISTS products (
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    product_id VARCHAR(64) NOT NULL,
    merchant_id VARCHAR(64) NOT NULL,
    name VARCHAR(255) NOT NULL,
    category VARCHAR(128) NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, product_id)
);

CREATE INDEX IF NOT EXISTS ix_products_tenant_category ON products(tenant_id, category);
CREATE INDEX IF NOT EXISTS ix_products_tenant_merchant ON products(tenant_id, merchant_id);
CREATE INDEX IF NOT EXISTS ix_products_tenant_search ON products(tenant_id, category, name);

-- 5. Product Variants Table (§30, §49)
CREATE TABLE IF NOT EXISTS product_variants (
    tenant_id VARCHAR(64) NOT NULL,
    variant_id VARCHAR(64) NOT NULL,
    product_id VARCHAR(64) NOT NULL,
    sku VARCHAR(128) NOT NULL,
    name VARCHAR(255) NOT NULL,
    price_minor BIGINT NOT NULL,
    price_currency VARCHAR(3) NOT NULL,
    PRIMARY KEY (tenant_id, variant_id),
    CONSTRAINT fk_product_variants_product
        FOREIGN KEY (tenant_id, product_id)
        REFERENCES products(tenant_id, product_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_product_variants_tenant_product ON product_variants(tenant_id, product_id);
CREATE INDEX IF NOT EXISTS ix_product_variants_tenant_sku ON product_variants(tenant_id, sku);

-- 6. Orders Table (§41, §44, §49)
CREATE TABLE IF NOT EXISTS orders (
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    order_id VARCHAR(64) NOT NULL,
    merchant_id VARCHAR(64) NOT NULL,
    buyer_id VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64) NOT NULL,
    variant_id VARCHAR(64) NOT NULL,
    quantity INTEGER NOT NULL,
    total_amount_minor BIGINT NOT NULL,
    total_amount_currency VARCHAR(3) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING_PAYMENT',
    payment_attempt_id VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, order_id)
);

CREATE INDEX IF NOT EXISTS ix_orders_tenant_status ON orders(tenant_id, status);
CREATE INDEX IF NOT EXISTS ix_orders_tenant_buyer ON orders(tenant_id, buyer_id);
CREATE INDEX IF NOT EXISTS ix_orders_tenant_agent ON orders(tenant_id, agent_id);
CREATE INDEX IF NOT EXISTS ix_orders_tenant_merchant ON orders(tenant_id, merchant_id);

-- 7. Payment Attempts Table (§39, §41, §42, §49)
CREATE TABLE IF NOT EXISTS payment_attempts (
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    attempt_id VARCHAR(64) NOT NULL,
    intent_id VARCHAR(64) NOT NULL,
    amount_minor BIGINT NOT NULL,
    amount_currency VARCHAR(3) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'CREATED',
    idempotency_key VARCHAR(128) NOT NULL,
    provider VARCHAR(64) NOT NULL DEFAULT 'mock',
    provider_payment_id VARCHAR(128),
    failure_reason VARCHAR(512),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tenant_id, attempt_id),
    CONSTRAINT uq_payment_attempts_tenant_idempotency UNIQUE (tenant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_payment_attempts_tenant_status ON payment_attempts(tenant_id, status);
CREATE INDEX IF NOT EXISTS ix_payment_attempts_tenant_intent ON payment_attempts(tenant_id, intent_id);

-- 8. Audit Events Table (§45, §46, §49)
CREATE TABLE IF NOT EXISTS audit_events (
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    event_id VARCHAR(64) NOT NULL,
    actor VARCHAR(128) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    previous_event_hash VARCHAR(64) NOT NULL,
    event_hash VARCHAR(64) NOT NULL,
    agent_id VARCHAR(64),
    action_id VARCHAR(64),
    request_id VARCHAR(64),
    correlation_id VARCHAR(64),
    trace_id VARCHAR(64),
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (tenant_id, event_id)
);

CREATE INDEX IF NOT EXISTS ix_audit_events_tenant_timestamp ON audit_events(tenant_id, timestamp);
CREATE INDEX IF NOT EXISTS ix_audit_events_tenant_action ON audit_events(tenant_id, action_id);
CREATE INDEX IF NOT EXISTS ix_audit_events_tenant_trace ON audit_events(tenant_id, trace_id);
CREATE INDEX IF NOT EXISTS ix_audit_events_tenant_event_type ON audit_events(tenant_id, event_type);
