-- v0.5.0: make tenant ownership part of database identity, not only application convention.

CREATE UNIQUE INDEX IF NOT EXISTS uq_control_runs_tenant_run
  ON control_runs (tenant_id, run_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_lineage_nodes_tenant_node
  ON lineage_nodes (tenant_id, node_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_reconciliation_results_tenant_id
  ON reconciliation_results (tenant_id, reconciliation_id);

CREATE INDEX IF NOT EXISTS ix_control_events_tenant_run_sequence
  ON control_events (tenant_id, run_id, sequence);
CREATE INDEX IF NOT EXISTS ix_lineage_nodes_tenant_run_kind
  ON lineage_nodes (tenant_id, run_id, kind);
CREATE INDEX IF NOT EXISTS ix_lineage_relations_tenant_run
  ON lineage_relations (tenant_id, run_id);
CREATE INDEX IF NOT EXISTS ix_execution_attempt_tenant_action
  ON execution_attempts (tenant_id, action_id, claimed_at DESC);
CREATE INDEX IF NOT EXISTS ix_reconciliation_findings_tenant_run
  ON reconciliation_findings (tenant_id, run_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS tenant_isolation_profiles (
  tenant_id VARCHAR(255) PRIMARY KEY,
  updated_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS support_elevation_grants (
  tenant_id VARCHAR(255) NOT NULL,
  grant_id UUID NOT NULL,
  operator_identity VARCHAR(1024) NOT NULL,
  issued_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, grant_id),
  CONSTRAINT ck_support_elevation_window CHECK (expires_at > issued_at),
  CONSTRAINT ck_support_elevation_revocation CHECK (
    revoked_at IS NULL OR revoked_at >= issued_at
  )
);

CREATE INDEX IF NOT EXISTS ix_support_elevation_tenant_expiry
  ON support_elevation_grants (tenant_id, expires_at)
  WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_support_elevation_operator
  ON support_elevation_grants (operator_identity, expires_at)
  WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS tenant_lifecycle (
  tenant_id VARCHAR(255) PRIMARY KEY,
  status VARCHAR(32) NOT NULL CHECK (
    status IN ('active', 'deletion_scheduled', 'deleted')
  ),
  legal_hold BOOLEAN NOT NULL DEFAULT FALSE,
  deletion_not_before TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  CONSTRAINT ck_tenant_lifecycle_deletion CHECK (
    (status = 'deletion_scheduled' AND deletion_not_before IS NOT NULL AND legal_hold = FALSE)
    OR
    (status <> 'deletion_scheduled' AND deletion_not_before IS NULL)
  ),
  CONSTRAINT ck_tenant_lifecycle_hold CHECK (
    status <> 'deleted' OR legal_hold = FALSE
  )
);

CREATE TABLE IF NOT EXISTS tenant_audit_events (
  audit_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  elevation_id UUID,
  document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tenant_audit_events_tenant_occurred
  ON tenant_audit_events (tenant_id, occurred_at, audit_id);
CREATE INDEX IF NOT EXISTS ix_tenant_audit_events_elevation
  ON tenant_audit_events (tenant_id, elevation_id, occurred_at)
  WHERE elevation_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS tenant_export_manifests (
  export_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tenant_export_manifests_tenant_created
  ON tenant_export_manifests (tenant_id, created_at, export_id);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_control_events_tenant_run') THEN
    ALTER TABLE control_events
      ADD CONSTRAINT fk_control_events_tenant_run
      FOREIGN KEY (tenant_id, run_id)
      REFERENCES control_runs (tenant_id, run_id)
      NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_lineage_nodes_tenant_run') THEN
    ALTER TABLE lineage_nodes
      ADD CONSTRAINT fk_lineage_nodes_tenant_run
      FOREIGN KEY (tenant_id, run_id)
      REFERENCES control_runs (tenant_id, run_id)
      NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_lineage_relations_subject_tenant') THEN
    ALTER TABLE lineage_relations
      ADD CONSTRAINT fk_lineage_relations_subject_tenant
      FOREIGN KEY (tenant_id, subject_node_id)
      REFERENCES lineage_nodes (tenant_id, node_id)
      NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_lineage_relations_object_tenant') THEN
    ALTER TABLE lineage_relations
      ADD CONSTRAINT fk_lineage_relations_object_tenant
      FOREIGN KEY (tenant_id, object_node_id)
      REFERENCES lineage_nodes (tenant_id, node_id)
      NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_execution_attempts_tenant_run') THEN
    ALTER TABLE execution_attempts
      ADD CONSTRAINT fk_execution_attempts_tenant_run
      FOREIGN KEY (tenant_id, run_id)
      REFERENCES control_runs (tenant_id, run_id)
      NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_reconciliation_results_tenant_run') THEN
    ALTER TABLE reconciliation_results
      ADD CONSTRAINT fk_reconciliation_results_tenant_run
      FOREIGN KEY (tenant_id, run_id)
      REFERENCES control_runs (tenant_id, run_id)
      NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_reconciliation_findings_tenant_result') THEN
    ALTER TABLE reconciliation_findings
      ADD CONSTRAINT fk_reconciliation_findings_tenant_result
      FOREIGN KEY (tenant_id, reconciliation_id)
      REFERENCES reconciliation_results (tenant_id, reconciliation_id)
      NOT VALID;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION prodkit_reject_tenant_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
    RAISE EXCEPTION 'tenant_id is immutable for %', TG_TABLE_NAME;
  END IF;
  RETURN NEW;
END;
$$;

DO $$
DECLARE
  table_name TEXT;
  trigger_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'control_runs',
    'idempotency_claims',
    'execution_attempts',
    'reconciliation_cursors',
    'reconciliation_results',
    'reconciliation_findings',
    'external_audit_events',
    'production_completeness_profiles',
    'work_leases',
    'durable_work_items',
    'tenant_isolation_profiles',
    'support_elevation_grants',
    'tenant_lifecycle',
    'tenant_audit_events',
    'tenant_export_manifests'
  ]
  LOOP
    trigger_name := table_name || '_tenant_immutable';
    IF NOT EXISTS (
      SELECT 1 FROM pg_trigger WHERE tgname = trigger_name AND NOT tgisinternal
    ) THEN
      EXECUTE format(
        'CREATE TRIGGER %I BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION prodkit_reject_tenant_change()',
        trigger_name,
        table_name
      );
    END IF;
  END LOOP;
END $$;

DROP TRIGGER IF EXISTS tenant_audit_events_no_update_delete ON tenant_audit_events;
CREATE TRIGGER tenant_audit_events_no_update_delete
BEFORE UPDATE OR DELETE ON tenant_audit_events
FOR EACH ROW EXECUTE FUNCTION prodkit_reject_append_only_mutation();

DROP TRIGGER IF EXISTS tenant_export_manifests_no_update_delete ON tenant_export_manifests;
CREATE TRIGGER tenant_export_manifests_no_update_delete
BEFORE UPDATE OR DELETE ON tenant_export_manifests
FOR EACH ROW EXECUTE FUNCTION prodkit_reject_append_only_mutation();

UPDATE prodkit_schema_metadata
SET version = 6,
    updated_at = CURRENT_TIMESTAMP
WHERE singleton = TRUE;

COMMENT ON FUNCTION prodkit_reject_tenant_change() IS
  'Database-level invariant: mutable tenant-owned rows cannot be reassigned between tenants.';
COMMENT ON TABLE tenant_isolation_profiles IS
  'Durable tenant-local selectors for policy, signing, retention, executors, storage and cache isolation.';
COMMENT ON TABLE support_elevation_grants IS
  'Time-bounded and capability-bounded audited support elevation grants for one tenant.';
COMMENT ON TABLE tenant_lifecycle IS
  'Durable tenant deletion and legal-hold state with fail-closed precedence rules.';
COMMENT ON TABLE tenant_audit_events IS
  'Append-only tenant administration, support-elevation, export and lifecycle audit evidence.';
COMMENT ON TABLE tenant_export_manifests IS
  'Append-only tenant export manifests; payload export storage remains adapter-specific.';
