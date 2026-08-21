CREATE TABLE IF NOT EXISTS idempotency_claims (
  tenant_id VARCHAR(255) NOT NULL,
  key VARCHAR(512) NOT NULL,
  action_digest CHAR(64) NOT NULL,
  state VARCHAR(32) NOT NULL CHECK (state IN ('claimed', 'completed')),
  claimed_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ NULL,
  result JSONB NULL,
  PRIMARY KEY (tenant_id, key),
  CONSTRAINT ck_idempotency_completion
    CHECK (
      (state = 'claimed' AND completed_at IS NULL AND result IS NULL) OR
      (state = 'completed' AND completed_at IS NOT NULL AND result IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_idempotency_tenant_state
  ON idempotency_claims (tenant_id, state, claimed_at);

CREATE TABLE IF NOT EXISTS execution_attempts (
  attempt_id UUID PRIMARY KEY,
  action_id UUID NOT NULL,
  run_id UUID NOT NULL,
  tenant_id VARCHAR(255) NOT NULL,
  idempotency_key VARCHAR(512) NOT NULL,
  action_digest CHAR(64) NOT NULL,
  executor_name VARCHAR(255) NOT NULL,
  executor_version VARCHAR(64) NOT NULL,
  executor_identity VARCHAR(512) NOT NULL,
  state VARCHAR(32) NOT NULL
    CHECK (state IN ('claimed', 'started', 'succeeded', 'failed', 'uncertain')),
  claimed_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ NULL,
  finished_at TIMESTAMPTZ NULL,
  document JSONB NOT NULL,
  CONSTRAINT ck_execution_attempt_timestamps CHECK (
    (state = 'claimed' AND started_at IS NULL AND finished_at IS NULL) OR
    (state = 'started' AND started_at IS NOT NULL AND finished_at IS NULL) OR
    (state IN ('succeeded', 'failed', 'uncertain')
      AND started_at IS NOT NULL AND finished_at IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS ix_execution_attempt_action
  ON execution_attempts (action_id, claimed_at DESC);
CREATE INDEX IF NOT EXISTS ix_execution_attempt_tenant_state
  ON execution_attempts (tenant_id, state, claimed_at);
CREATE INDEX IF NOT EXISTS ix_execution_attempt_idempotency
  ON execution_attempts (tenant_id, idempotency_key);

CREATE OR REPLACE FUNCTION prodkit_validate_idempotency_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'idempotency claims cannot be deleted';
  END IF;
  IF NEW.tenant_id <> OLD.tenant_id
     OR NEW.key <> OLD.key
     OR NEW.action_digest <> OLD.action_digest
     OR NEW.claimed_at <> OLD.claimed_at THEN
    RAISE EXCEPTION 'idempotency claim identity is immutable';
  END IF;
  IF OLD.state = 'completed' AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'completed idempotency claims are immutable';
  END IF;
  IF OLD.state = 'claimed' AND NEW.state NOT IN ('claimed', 'completed') THEN
    RAISE EXCEPTION 'illegal idempotency transition: % -> %', OLD.state, NEW.state;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS idempotency_claims_guard ON idempotency_claims;
CREATE TRIGGER idempotency_claims_guard
BEFORE UPDATE OR DELETE ON idempotency_claims
FOR EACH ROW EXECUTE FUNCTION prodkit_validate_idempotency_update();

CREATE OR REPLACE FUNCTION prodkit_validate_execution_attempt_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'execution attempts cannot be deleted';
  END IF;
  IF NEW.attempt_id <> OLD.attempt_id
     OR NEW.action_id <> OLD.action_id
     OR NEW.run_id <> OLD.run_id
     OR NEW.tenant_id <> OLD.tenant_id
     OR NEW.idempotency_key <> OLD.idempotency_key
     OR NEW.action_digest <> OLD.action_digest
     OR NEW.executor_name <> OLD.executor_name
     OR NEW.executor_version <> OLD.executor_version
     OR NEW.executor_identity <> OLD.executor_identity
     OR NEW.claimed_at <> OLD.claimed_at THEN
    RAISE EXCEPTION 'execution attempt identity is immutable';
  END IF;
  IF OLD.state = 'claimed' AND NEW.state <> 'started' THEN
    RAISE EXCEPTION 'illegal execution-attempt transition: % -> %', OLD.state, NEW.state;
  END IF;
  IF OLD.state = 'started' AND NEW.state NOT IN ('succeeded', 'failed', 'uncertain') THEN
    RAISE EXCEPTION 'illegal execution-attempt transition: % -> %', OLD.state, NEW.state;
  END IF;
  IF OLD.state IN ('succeeded', 'failed', 'uncertain') AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'terminal execution attempts are immutable';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS execution_attempts_guard ON execution_attempts;
CREATE TRIGGER execution_attempts_guard
BEFORE UPDATE OR DELETE ON execution_attempts
FOR EACH ROW EXECUTE FUNCTION prodkit_validate_execution_attempt_update();

COMMENT ON TABLE idempotency_claims IS
  'Durable tenant-scoped ownership of externally intended actions; uncertain claims remain owned.';
COMMENT ON TABLE execution_attempts IS
  'Durable pre-side-effect execution journal with explicit terminal uncertainty.';
