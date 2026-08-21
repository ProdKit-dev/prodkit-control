CREATE TABLE IF NOT EXISTS control_runs (
  run_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  status VARCHAR(64) NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_control_runs_tenant_status
  ON control_runs (tenant_id, status, started_at);

CREATE TABLE IF NOT EXISTS prodkit_schema_metadata (
  singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton = TRUE),
  version INTEGER NOT NULL CHECK (version > 0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO prodkit_schema_metadata (singleton, version)
VALUES (TRUE, 3)
ON CONFLICT (singleton) DO UPDATE
SET version = EXCLUDED.version,
    updated_at = CURRENT_TIMESTAMP;

COMMENT ON TABLE control_runs IS
  'Durable current-state projection for ProdKit Control runs.';
COMMENT ON TABLE prodkit_schema_metadata IS
  'Single-row runtime/schema compatibility marker maintained by ordered migrations.';
