CREATE TABLE IF NOT EXISTS work_leases (
  tenant_id VARCHAR(255) NOT NULL,
  resource_key VARCHAR(512) NOT NULL,
  fence_token BIGINT NOT NULL DEFAULT 0 CHECK (fence_token >= 0),
  lease_id UUID,
  owner_id VARCHAR(512),
  acquired_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (tenant_id, resource_key),
  CONSTRAINT ck_work_leases_binding CHECK (
    (lease_id IS NULL AND owner_id IS NULL AND acquired_at IS NULL AND expires_at IS NULL)
    OR
    (lease_id IS NOT NULL AND owner_id IS NOT NULL AND acquired_at IS NOT NULL AND expires_at IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS ix_work_leases_expiry
  ON work_leases (expires_at)
  WHERE lease_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS durable_work_items (
  job_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  queue VARCHAR(255) NOT NULL,
  kind VARCHAR(255) NOT NULL,
  idempotency_key VARCHAR(512) NOT NULL,
  state VARCHAR(32) NOT NULL CHECK (state IN ('queued', 'leased', 'succeeded', 'dead_letter')),
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  available_at TIMESTAMPTZ NOT NULL,
  attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1 AND max_attempts <= 1000),
  lease_id UUID,
  lease_owner_id VARCHAR(512),
  lease_fence_token BIGINT NOT NULL DEFAULT 0 CHECK (lease_fence_token >= 0),
  lease_acquired_at TIMESTAMPTZ,
  lease_expires_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  last_error TEXT,
  CONSTRAINT uq_durable_work_identity UNIQUE (tenant_id, queue, idempotency_key),
  CONSTRAINT ck_durable_work_lease_binding CHECK (
    (state = 'leased' AND lease_id IS NOT NULL AND lease_owner_id IS NOT NULL
      AND lease_acquired_at IS NOT NULL AND lease_expires_at IS NOT NULL)
    OR
    (state <> 'leased' AND lease_id IS NULL AND lease_owner_id IS NULL
      AND lease_acquired_at IS NULL AND lease_expires_at IS NULL)
  ),
  CONSTRAINT ck_durable_work_completion CHECK (
    (state = 'succeeded' AND completed_at IS NOT NULL)
    OR (state <> 'succeeded' AND completed_at IS NULL)
  ),
  CONSTRAINT ck_durable_work_dead_letter CHECK (
    state <> 'dead_letter' OR last_error IS NOT NULL
  )
);

CREATE INDEX IF NOT EXISTS ix_durable_work_available
  ON durable_work_items (queue, state, available_at, created_at);
CREATE INDEX IF NOT EXISTS ix_durable_work_tenant_available
  ON durable_work_items (tenant_id, queue, state, available_at, created_at);
CREATE INDEX IF NOT EXISTS ix_durable_work_lease_expiry
  ON durable_work_items (queue, lease_expires_at)
  WHERE state = 'leased';

UPDATE prodkit_schema_metadata
SET version = 5,
    updated_at = CURRENT_TIMESTAMP
WHERE singleton = TRUE;

COMMENT ON TABLE work_leases IS
  'Reusable single-owner leases whose monotonically increasing fence token survives expiry/release.';
COMMENT ON TABLE durable_work_items IS
  'Bounded recoverable scheduler state. Stale owners cannot mutate work after a higher fence is issued.';
