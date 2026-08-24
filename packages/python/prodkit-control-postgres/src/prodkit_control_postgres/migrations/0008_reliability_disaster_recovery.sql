-- v0.7.0: durable reliability, backup/restore, break-glass, and DR evidence.

CREATE TABLE IF NOT EXISTS recovery_profiles (
  tenant_id VARCHAR(255) NOT NULL,
  profile_id VARCHAR(255) NOT NULL,
  revision INTEGER NOT NULL CHECK (revision >= 1),
  effective_at TIMESTAMPTZ NOT NULL,
  rpo_seconds INTEGER NOT NULL CHECK (rpo_seconds >= 0),
  rto_seconds INTEGER NOT NULL CHECK (rto_seconds > 0),
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, profile_id, revision),
  CONSTRAINT uq_recovery_profile_tenant_revision UNIQUE (tenant_id, revision)
);

CREATE INDEX IF NOT EXISTS ix_recovery_profile_effective
  ON recovery_profiles (tenant_id, effective_at DESC, revision DESC);

CREATE TABLE IF NOT EXISTS recovery_backup_manifests (
  tenant_id VARCHAR(255) NOT NULL,
  backup_id UUID NOT NULL,
  profile_id VARCHAR(255) NOT NULL,
  profile_revision INTEGER NOT NULL CHECK (profile_revision >= 1),
  source_schema_version INTEGER NOT NULL CHECK (source_schema_version >= 1),
  recovery_point_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  trust_anchor_sha256 CHAR(64) NOT NULL CHECK (trust_anchor_sha256 ~ '^[0-9a-f]{64}$'),
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, backup_id),
  CONSTRAINT fk_recovery_backup_profile
    FOREIGN KEY (tenant_id, profile_id, profile_revision)
    REFERENCES recovery_profiles (tenant_id, profile_id, revision),
  CONSTRAINT ck_recovery_backup_point CHECK (recovery_point_at <= created_at)
);

CREATE INDEX IF NOT EXISTS ix_recovery_backup_point
  ON recovery_backup_manifests (tenant_id, recovery_point_at DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS recovery_break_glass_grants (
  tenant_id VARCHAR(255) NOT NULL,
  grant_id UUID NOT NULL,
  operator_id VARCHAR(1024) NOT NULL,
  approved_by_id VARCHAR(1024) NOT NULL,
  issued_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, grant_id),
  CONSTRAINT ck_recovery_break_glass_window CHECK (expires_at > issued_at),
  CONSTRAINT ck_recovery_break_glass_four_eyes CHECK (operator_id <> approved_by_id)
);

CREATE INDEX IF NOT EXISTS ix_recovery_break_glass_active
  ON recovery_break_glass_grants (tenant_id, expires_at, grant_id);

CREATE TABLE IF NOT EXISTS recovery_break_glass_uses (
  use_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  grant_id UUID NOT NULL,
  capability VARCHAR(64) NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  CONSTRAINT fk_recovery_break_glass_use
    FOREIGN KEY (tenant_id, grant_id)
    REFERENCES recovery_break_glass_grants (tenant_id, grant_id)
);

CREATE INDEX IF NOT EXISTS ix_recovery_break_glass_use_grant
  ON recovery_break_glass_uses (tenant_id, grant_id, occurred_at, use_id);

CREATE TABLE IF NOT EXISTS recovery_break_glass_revocations (
  revocation_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  grant_id UUID NOT NULL,
  revoked_at TIMESTAMPTZ NOT NULL,
  actor_id VARCHAR(1024) NOT NULL,
  reason VARCHAR(4096) NOT NULL,
  CONSTRAINT fk_recovery_break_glass_revocation
    FOREIGN KEY (tenant_id, grant_id)
    REFERENCES recovery_break_glass_grants (tenant_id, grant_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_recovery_break_glass_revoked
  ON recovery_break_glass_revocations (tenant_id, grant_id);

CREATE TABLE IF NOT EXISTS recovery_restore_plans (
  tenant_id VARCHAR(255) NOT NULL,
  restore_id UUID NOT NULL,
  backup_id UUID NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL,
  target_site VARCHAR(1024) NOT NULL,
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, restore_id),
  CONSTRAINT fk_recovery_restore_backup
    FOREIGN KEY (tenant_id, backup_id)
    REFERENCES recovery_backup_manifests (tenant_id, backup_id)
);

CREATE INDEX IF NOT EXISTS ix_recovery_restore_requested
  ON recovery_restore_plans (tenant_id, requested_at DESC, restore_id);

CREATE TABLE IF NOT EXISTS recovery_integrity_scans (
  scan_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  restore_id UUID NOT NULL,
  status VARCHAR(16) NOT NULL CHECK (status IN ('verified', 'failed')),
  completed_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  CONSTRAINT fk_recovery_integrity_restore
    FOREIGN KEY (tenant_id, restore_id)
    REFERENCES recovery_restore_plans (tenant_id, restore_id)
);

CREATE INDEX IF NOT EXISTS ix_recovery_integrity_restore
  ON recovery_integrity_scans (tenant_id, restore_id, completed_at DESC, scan_id);

CREATE TABLE IF NOT EXISTS recovery_uncertain_executions (
  recovery_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  restore_id UUID NOT NULL,
  attempt_id UUID NOT NULL,
  action_id UUID NOT NULL,
  disposition VARCHAR(32) NOT NULL CHECK (
    disposition IN ('reconcile_required', 'matched_success', 'matched_failure', 'not_observed', 'unverifiable')
  ),
  observed_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  CONSTRAINT fk_recovery_uncertain_restore
    FOREIGN KEY (tenant_id, restore_id)
    REFERENCES recovery_restore_plans (tenant_id, restore_id)
);

CREATE INDEX IF NOT EXISTS ix_recovery_uncertain_restore
  ON recovery_uncertain_executions (tenant_id, restore_id, observed_at, recovery_id);
CREATE INDEX IF NOT EXISTS ix_recovery_uncertain_attempt
  ON recovery_uncertain_executions (tenant_id, attempt_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS recovery_restore_results (
  tenant_id VARCHAR(255) NOT NULL,
  restore_id UUID NOT NULL,
  backup_id UUID NOT NULL,
  status VARCHAR(16) NOT NULL CHECK (status IN ('verified', 'degraded', 'failed')),
  completed_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  PRIMARY KEY (tenant_id, restore_id),
  CONSTRAINT fk_recovery_result_plan
    FOREIGN KEY (tenant_id, restore_id)
    REFERENCES recovery_restore_plans (tenant_id, restore_id),
  CONSTRAINT fk_recovery_result_backup
    FOREIGN KEY (tenant_id, backup_id)
    REFERENCES recovery_backup_manifests (tenant_id, backup_id)
);

CREATE INDEX IF NOT EXISTS ix_recovery_result_status
  ON recovery_restore_results (tenant_id, status, completed_at DESC);

CREATE TABLE IF NOT EXISTS recovery_game_day_exercises (
  exercise_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  restore_id UUID NOT NULL,
  backup_id UUID NOT NULL,
  passed BOOLEAN NOT NULL,
  completed_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  CONSTRAINT fk_recovery_game_day_restore
    FOREIGN KEY (tenant_id, restore_id)
    REFERENCES recovery_restore_plans (tenant_id, restore_id),
  CONSTRAINT fk_recovery_game_day_backup
    FOREIGN KEY (tenant_id, backup_id)
    REFERENCES recovery_backup_manifests (tenant_id, backup_id)
);

CREATE INDEX IF NOT EXISTS ix_recovery_game_day_tenant
  ON recovery_game_day_exercises (tenant_id, completed_at DESC, exercise_id);

CREATE TABLE IF NOT EXISTS recovery_audit_events (
  event_id UUID PRIMARY KEY,
  tenant_id VARCHAR(255) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  target_id VARCHAR(1024) NOT NULL,
  document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_recovery_audit_tenant_occurred
  ON recovery_audit_events (tenant_id, occurred_at, event_id);

CREATE OR REPLACE FUNCTION prodkit_recovery_reject_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'recovery evidence is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_recovery_profiles_append_only_v8 ON recovery_profiles;
CREATE TRIGGER trg_recovery_profiles_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_profiles
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

DROP TRIGGER IF EXISTS trg_recovery_backup_append_only_v8 ON recovery_backup_manifests;
CREATE TRIGGER trg_recovery_backup_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_backup_manifests
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

DROP TRIGGER IF EXISTS trg_recovery_break_glass_grant_append_only_v8 ON recovery_break_glass_grants;
CREATE TRIGGER trg_recovery_break_glass_grant_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_break_glass_grants
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

DROP TRIGGER IF EXISTS trg_recovery_break_glass_use_append_only_v8 ON recovery_break_glass_uses;
CREATE TRIGGER trg_recovery_break_glass_use_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_break_glass_uses
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

DROP TRIGGER IF EXISTS trg_recovery_break_glass_revocation_append_only_v8 ON recovery_break_glass_revocations;
CREATE TRIGGER trg_recovery_break_glass_revocation_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_break_glass_revocations
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

DROP TRIGGER IF EXISTS trg_recovery_restore_plan_append_only_v8 ON recovery_restore_plans;
CREATE TRIGGER trg_recovery_restore_plan_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_restore_plans
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

DROP TRIGGER IF EXISTS trg_recovery_integrity_append_only_v8 ON recovery_integrity_scans;
CREATE TRIGGER trg_recovery_integrity_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_integrity_scans
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

DROP TRIGGER IF EXISTS trg_recovery_uncertain_append_only_v8 ON recovery_uncertain_executions;
CREATE TRIGGER trg_recovery_uncertain_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_uncertain_executions
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

DROP TRIGGER IF EXISTS trg_recovery_result_append_only_v8 ON recovery_restore_results;
CREATE TRIGGER trg_recovery_result_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_restore_results
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

DROP TRIGGER IF EXISTS trg_recovery_game_day_append_only_v8 ON recovery_game_day_exercises;
CREATE TRIGGER trg_recovery_game_day_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_game_day_exercises
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

DROP TRIGGER IF EXISTS trg_recovery_audit_append_only_v8 ON recovery_audit_events;
CREATE TRIGGER trg_recovery_audit_append_only_v8
BEFORE UPDATE OR DELETE ON recovery_audit_events
FOR EACH ROW EXECUTE FUNCTION prodkit_recovery_reject_mutation();

UPDATE prodkit_schema_metadata
SET version = 8, updated_at = clock_timestamp()
WHERE singleton = TRUE AND version = 7;

DO $$
BEGIN
  IF (SELECT version FROM prodkit_schema_metadata WHERE singleton = TRUE) <> 8 THEN
    RAISE EXCEPTION 'schema 8 migration requires schema 7 as its source';
  END IF;
END;
$$;
