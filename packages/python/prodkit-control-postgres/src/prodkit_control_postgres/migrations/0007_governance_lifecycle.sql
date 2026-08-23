-- v0.6.0: durable governance, retention, legal-hold, trust-root, and migration evidence.

CREATE TABLE IF NOT EXISTS governance_change_requests (
  tenant_id VARCHAR(255) NOT NULL,
  request_id UUID NOT NULL,
  target_type VARCHAR(64) NOT NULL,
  target_id VARCHAR(1024) NOT NULL,
  proposed_digest CHAR(64) NOT NULL,
  expected_current_digest CHAR(64),
  risk VARCHAR(16) NOT NULL CHECK (risk IN ('low', 'medium', 'high', 'critical')),
  status VARCHAR(16) NOT NULL CHECK (
    status IN ('proposed', 'approved', 'rejected', 'applied', 'cancelled')
  ),
  proposed_at TIMESTAMPTZ NOT NULL,
  approved_at TIMESTAMPTZ,
  applied_at TIMESTAMPTZ,
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, request_id),
  CONSTRAINT ck_governance_request_digest CHECK (proposed_digest ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_governance_request_current_digest CHECK (
    expected_current_digest IS NULL OR expected_current_digest ~ '^[0-9a-f]{64}$'
  )
);

CREATE INDEX IF NOT EXISTS ix_governance_change_tenant_status
  ON governance_change_requests (tenant_id, status, proposed_at, request_id);
CREATE INDEX IF NOT EXISTS ix_governance_change_target
  ON governance_change_requests (tenant_id, target_type, target_id, proposed_at DESC);

CREATE TABLE IF NOT EXISTS governance_approvals (
  approval_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  request_id UUID NOT NULL,
  decision VARCHAR(16) NOT NULL CHECK (decision IN ('approve', 'reject')),
  occurred_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  CONSTRAINT fk_governance_approval_request
    FOREIGN KEY (tenant_id, request_id)
    REFERENCES governance_change_requests (tenant_id, request_id)
);

CREATE INDEX IF NOT EXISTS ix_governance_approval_request
  ON governance_approvals (tenant_id, request_id, occurred_at, approval_id);

CREATE TABLE IF NOT EXISTS governance_retention_policies (
  tenant_id VARCHAR(255) NOT NULL,
  policy_id UUID NOT NULL,
  revision INTEGER NOT NULL CHECK (revision >= 1),
  effective_at TIMESTAMPTZ NOT NULL,
  policy_sha256 CHAR(64) NOT NULL CHECK (policy_sha256 ~ '^[0-9a-f]{64}$'),
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, policy_id, revision),
  CONSTRAINT uq_governance_retention_tenant_revision UNIQUE (tenant_id, revision)
);

CREATE INDEX IF NOT EXISTS ix_governance_retention_effective
  ON governance_retention_policies (tenant_id, effective_at DESC, revision DESC);

CREATE TABLE IF NOT EXISTS governance_legal_holds (
  tenant_id VARCHAR(255) NOT NULL,
  hold_id UUID NOT NULL,
  status VARCHAR(16) NOT NULL CHECK (status IN ('active', 'released')),
  placed_at TIMESTAMPTZ NOT NULL,
  released_at TIMESTAMPTZ,
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, hold_id),
  CONSTRAINT ck_governance_hold_release CHECK (
    (status = 'active' AND released_at IS NULL)
    OR (status = 'released' AND released_at IS NOT NULL AND released_at >= placed_at)
  )
);

CREATE INDEX IF NOT EXISTS ix_governance_hold_active
  ON governance_legal_holds (tenant_id, placed_at, hold_id)
  WHERE status = 'active';

CREATE TABLE IF NOT EXISTS governance_trust_roots (
  tenant_id VARCHAR(255) NOT NULL,
  revision INTEGER NOT NULL CHECK (revision >= 1),
  policy_id VARCHAR(1024) NOT NULL,
  policy_sha256 CHAR(64) NOT NULL CHECK (policy_sha256 ~ '^[0-9a-f]{64}$'),
  activated_at TIMESTAMPTZ NOT NULL,
  retired_at TIMESTAMPTZ,
  change_request_id UUID NOT NULL,
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, revision),
  CONSTRAINT ck_governance_trust_root_window CHECK (
    retired_at IS NULL OR retired_at > activated_at
  ),
  CONSTRAINT fk_governance_trust_root_change
    FOREIGN KEY (tenant_id, change_request_id)
    REFERENCES governance_change_requests (tenant_id, request_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_governance_trust_root_current
  ON governance_trust_roots (tenant_id)
  WHERE retired_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_governance_trust_root_window
  ON governance_trust_roots (tenant_id, activated_at, retired_at, revision);

CREATE TABLE IF NOT EXISTS governance_evidence_transfers (
  tenant_id VARCHAR(255) NOT NULL,
  transfer_id UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  archive_sha256 CHAR(64) NOT NULL CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, transfer_id)
);

CREATE INDEX IF NOT EXISTS ix_governance_transfer_tenant_created
  ON governance_evidence_transfers (tenant_id, created_at, transfer_id);

CREATE TABLE IF NOT EXISTS governance_evidence_imports (
  import_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  source_transfer_id UUID NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL,
  archive_sha256 CHAR(64) NOT NULL CHECK (archive_sha256 ~ '^[0-9a-f]{64}$'),
  source_schema_version INTEGER NOT NULL CHECK (source_schema_version >= 1),
  document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_governance_import_tenant_created
  ON governance_evidence_imports (tenant_id, imported_at, import_id);
CREATE INDEX IF NOT EXISTS ix_governance_import_source
  ON governance_evidence_imports (tenant_id, source_transfer_id);

CREATE TABLE IF NOT EXISTS governance_retention_executions (
  execution_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  resource_type VARCHAR(255) NOT NULL,
  resource_id VARCHAR(1024) NOT NULL,
  executed_at TIMESTAMPTZ NOT NULL,
  policy_id UUID NOT NULL,
  policy_revision INTEGER NOT NULL CHECK (policy_revision >= 1),
  document JSONB NOT NULL,
  CONSTRAINT fk_governance_retention_policy
    FOREIGN KEY (tenant_id, policy_id, policy_revision)
    REFERENCES governance_retention_policies (tenant_id, policy_id, revision)
);

CREATE INDEX IF NOT EXISTS ix_governance_retention_execution_resource
  ON governance_retention_executions (tenant_id, resource_type, resource_id, executed_at);

CREATE TABLE IF NOT EXISTS governance_audit_events (
  event_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  request_id UUID,
  document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_governance_audit_tenant_occurred
  ON governance_audit_events (tenant_id, occurred_at, event_id);
CREATE INDEX IF NOT EXISTS ix_governance_audit_request
  ON governance_audit_events (tenant_id, request_id, occurred_at)
  WHERE request_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS governance_migration_evidence (
  migration_id UUID PRIMARY KEY,
  from_schema_version INTEGER NOT NULL CHECK (from_schema_version >= 1),
  to_schema_version INTEGER NOT NULL CHECK (to_schema_version = from_schema_version + 1),
  applied_at TIMESTAMPTZ NOT NULL,
  control_version VARCHAR(64) NOT NULL,
  backup_reference VARCHAR(2048),
  document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_governance_migration_versions
  ON governance_migration_evidence (from_schema_version, to_schema_version, applied_at);

CREATE OR REPLACE FUNCTION prodkit_validate_governance_change_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
     OR NEW.request_id IS DISTINCT FROM OLD.request_id
     OR NEW.target_type IS DISTINCT FROM OLD.target_type
     OR NEW.target_id IS DISTINCT FROM OLD.target_id
     OR NEW.proposed_digest IS DISTINCT FROM OLD.proposed_digest
     OR NEW.expected_current_digest IS DISTINCT FROM OLD.expected_current_digest
     OR NEW.risk IS DISTINCT FROM OLD.risk
     OR NEW.proposed_at IS DISTINCT FROM OLD.proposed_at THEN
    RAISE EXCEPTION 'governance change identity and proposal are immutable';
  END IF;
  IF (NEW.document - ARRAY['status','approved_at','approved_by','applied_at'])
     IS DISTINCT FROM
     (OLD.document - ARRAY['status','approved_at','approved_by','applied_at']) THEN
    RAISE EXCEPTION 'governance change proposal document is immutable';
  END IF;
  IF OLD.status = 'proposed' AND NEW.status = 'approved'
     AND NEW.approved_at IS NOT NULL AND NEW.applied_at IS NULL THEN
    RETURN NEW;
  END IF;
  IF OLD.status = 'proposed' AND NEW.status IN ('rejected', 'cancelled')
     AND NEW.applied_at IS NULL THEN
    RETURN NEW;
  END IF;
  IF OLD.status = 'approved' AND NEW.status = 'applied'
     AND NEW.approved_at IS NOT NULL AND NEW.applied_at IS NOT NULL THEN
    RETURN NEW;
  END IF;
  IF OLD.status = 'approved' AND NEW.status = 'cancelled' AND NEW.applied_at IS NULL THEN
    RETURN NEW;
  END IF;
  IF OLD.status = NEW.status
     AND NEW.document IS NOT DISTINCT FROM OLD.document
     AND NEW.approved_at IS NOT DISTINCT FROM OLD.approved_at
     AND NEW.applied_at IS NOT DISTINCT FROM OLD.applied_at THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'invalid governance change transition % -> %', OLD.status, NEW.status;
END;
$$;

CREATE OR REPLACE FUNCTION prodkit_validate_legal_hold_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
     OR NEW.hold_id IS DISTINCT FROM OLD.hold_id
     OR NEW.placed_at IS DISTINCT FROM OLD.placed_at THEN
    RAISE EXCEPTION 'legal hold identity and placement are immutable';
  END IF;
  IF (NEW.document - ARRAY['status','released_at','released_by','release_change_request_id'])
     IS DISTINCT FROM
     (OLD.document - ARRAY['status','released_at','released_by','release_change_request_id']) THEN
    RAISE EXCEPTION 'legal hold scope and placement document are immutable';
  END IF;
  IF OLD.status = 'active' AND NEW.status = 'released' AND NEW.released_at IS NOT NULL THEN
    RETURN NEW;
  END IF;
  IF OLD.status = NEW.status AND NEW.document IS NOT DISTINCT FROM OLD.document THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'invalid legal hold transition % -> %', OLD.status, NEW.status;
END;
$$;

CREATE OR REPLACE FUNCTION prodkit_validate_trust_root_retirement()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
     OR NEW.revision IS DISTINCT FROM OLD.revision
     OR NEW.policy_id IS DISTINCT FROM OLD.policy_id
     OR NEW.policy_sha256 IS DISTINCT FROM OLD.policy_sha256
     OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
     OR NEW.change_request_id IS DISTINCT FROM OLD.change_request_id THEN
    RAISE EXCEPTION 'trust-root identity and policy are immutable';
  END IF;
  IF (NEW.document - 'retired_at') IS DISTINCT FROM (OLD.document - 'retired_at') THEN
    RAISE EXCEPTION 'trust-root policy document is immutable';
  END IF;
  IF OLD.retired_at IS NULL AND NEW.retired_at IS NOT NULL AND NEW.retired_at > OLD.activated_at THEN
    RETURN NEW;
  END IF;
  IF OLD.retired_at IS NOT DISTINCT FROM NEW.retired_at
     AND NEW.document IS NOT DISTINCT FROM OLD.document THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'trust-root retirement is the only permitted mutation';
END;
$$;

CREATE OR REPLACE FUNCTION prodkit_block_tenant_deletion_with_governance_hold()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status IN ('deletion_scheduled', 'deleted')
     AND EXISTS (
       SELECT 1
       FROM governance_legal_holds
       WHERE tenant_id = NEW.tenant_id AND status = 'active'
     ) THEN
    RAISE EXCEPTION 'tenant deletion is blocked by active governance legal hold';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS governance_change_transition ON governance_change_requests;
CREATE TRIGGER governance_change_transition
BEFORE UPDATE ON governance_change_requests
FOR EACH ROW EXECUTE FUNCTION prodkit_validate_governance_change_transition();

DROP TRIGGER IF EXISTS governance_hold_transition ON governance_legal_holds;
CREATE TRIGGER governance_hold_transition
BEFORE UPDATE ON governance_legal_holds
FOR EACH ROW EXECUTE FUNCTION prodkit_validate_legal_hold_transition();

DROP TRIGGER IF EXISTS governance_trust_root_retirement ON governance_trust_roots;
CREATE TRIGGER governance_trust_root_retirement
BEFORE UPDATE ON governance_trust_roots
FOR EACH ROW EXECUTE FUNCTION prodkit_validate_trust_root_retirement();

DROP TRIGGER IF EXISTS tenant_lifecycle_governance_hold_guard ON tenant_lifecycle;
CREATE TRIGGER tenant_lifecycle_governance_hold_guard
BEFORE INSERT OR UPDATE ON tenant_lifecycle
FOR EACH ROW EXECUTE FUNCTION prodkit_block_tenant_deletion_with_governance_hold();

DO $$
DECLARE
  table_name TEXT;
  trigger_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'governance_change_requests',
    'governance_approvals',
    'governance_retention_policies',
    'governance_legal_holds',
    'governance_trust_roots',
    'governance_evidence_transfers',
    'governance_evidence_imports',
    'governance_retention_executions',
    'governance_audit_events'
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

DO $$
DECLARE
  table_name TEXT;
  trigger_name TEXT;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'governance_approvals',
    'governance_retention_policies',
    'governance_evidence_transfers',
    'governance_evidence_imports',
    'governance_retention_executions',
    'governance_audit_events',
    'governance_migration_evidence'
  ]
  LOOP
    trigger_name := table_name || '_append_only';
    IF NOT EXISTS (
      SELECT 1 FROM pg_trigger WHERE tgname = trigger_name AND NOT tgisinternal
    ) THEN
      EXECUTE format(
        'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION prodkit_reject_append_only_mutation()',
        trigger_name,
        table_name
      );
    END IF;
  END LOOP;
END $$;

UPDATE prodkit_schema_metadata
SET version = 7,
    updated_at = CURRENT_TIMESTAMP
WHERE singleton = TRUE;

COMMENT ON TABLE governance_change_requests IS
  'Digest-bound governed changes with constrained state transitions and independent approval for high-risk operations.';
COMMENT ON TABLE governance_retention_policies IS
  'Append-only tenant retention policy revisions used for deterministic retention and deletion decisions.';
COMMENT ON TABLE governance_legal_holds IS
  'Tenant-scoped legal holds; release is the only valid lifecycle mutation and is governed separately.';
COMMENT ON TABLE governance_trust_roots IS
  'Versioned tenant trust-root history retaining historical verification windows across key rotations.';
COMMENT ON TABLE governance_audit_events IS
  'Append-only governance evidence for policy, hold, trust-root, transfer, and retention operations.';
COMMENT ON TABLE governance_migration_evidence IS
  'Append-only deployment migration evidence for supported sequential schema upgrades.';
