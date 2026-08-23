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
    'durable_work_items'
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

UPDATE prodkit_schema_metadata
SET version = 6,
    updated_at = CURRENT_TIMESTAMP
WHERE singleton = TRUE;

COMMENT ON FUNCTION prodkit_reject_tenant_change() IS
  'Database-level invariant: mutable tenant-owned rows cannot be reassigned between tenants.';
