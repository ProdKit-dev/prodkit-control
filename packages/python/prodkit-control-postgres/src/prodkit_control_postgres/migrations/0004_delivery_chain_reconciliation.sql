CREATE TABLE IF NOT EXISTS reconciliation_cursors (
  tenant_id VARCHAR(255) NOT NULL,
  source_system VARCHAR(255) NOT NULL,
  cursor TEXT,
  high_watermark TIMESTAMPTZ,
  health VARCHAR(32) NOT NULL,
  consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
  next_attempt_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, source_system)
);

CREATE TABLE IF NOT EXISTS reconciliation_results (
  reconciliation_id UUID PRIMARY KEY,
  run_id UUID NOT NULL,
  tenant_id VARCHAR(255) NOT NULL,
  source_system VARCHAR(255) NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL,
  health VARCHAR(32) NOT NULL,
  document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_reconciliation_results_tenant_source
  ON reconciliation_results (tenant_id, source_system, completed_at DESC);

CREATE TABLE IF NOT EXISTS reconciliation_findings (
  finding_id UUID PRIMARY KEY,
  reconciliation_id UUID NOT NULL REFERENCES reconciliation_results(reconciliation_id),
  run_id UUID NOT NULL,
  action_id UUID,
  tenant_id VARCHAR(255) NOT NULL,
  source_system VARCHAR(255) NOT NULL,
  outcome VARCHAR(64) NOT NULL,
  severity VARCHAR(32) NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_reconciliation_findings_tenant_observed
  ON reconciliation_findings (tenant_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS ix_reconciliation_findings_action
  ON reconciliation_findings (action_id) WHERE action_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS external_audit_events (
  tenant_id VARCHAR(255) NOT NULL,
  source_system VARCHAR(255) NOT NULL,
  event_id VARCHAR(512) NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  payload_digest VARCHAR(64) NOT NULL,
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, source_system, event_id)
);

CREATE INDEX IF NOT EXISTS ix_external_audit_events_tenant_occurred
  ON external_audit_events (tenant_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS production_completeness_profiles (
  tenant_id VARCHAR(255) NOT NULL,
  profile_id VARCHAR(255) NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, profile_id)
);

UPDATE prodkit_schema_metadata
SET version = 4,
    updated_at = CURRENT_TIMESTAMP
WHERE singleton = TRUE;

COMMENT ON TABLE reconciliation_cursors IS
  'Durable per-tenant source cursor, health, scheduling and backoff state.';
COMMENT ON TABLE reconciliation_results IS
  'Immutable reconciliation execution summaries.';
COMMENT ON TABLE reconciliation_findings IS
  'Deterministic reconciliation findings, including bypass and unverifiable states.';
COMMENT ON TABLE external_audit_events IS
  'Deduplicated external audit evidence ingested from delivery-chain providers.';
COMMENT ON TABLE production_completeness_profiles IS
  'Tenant-scoped production completeness requirements.';
