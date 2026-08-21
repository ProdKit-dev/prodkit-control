CREATE TABLE IF NOT EXISTS control_events (
  id BIGSERIAL PRIMARY KEY,
  event_id UUID NOT NULL UNIQUE,
  run_id UUID NOT NULL,
  action_id UUID NULL,
  tenant_id VARCHAR(255) NOT NULL,
  sequence BIGINT NOT NULL,
  event_type VARCHAR(100) NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL,
  previous_event_hash CHAR(64) NULL,
  event_hash CHAR(64) NOT NULL,
  document JSONB NOT NULL,
  CONSTRAINT uq_control_events_run_sequence UNIQUE (run_id, sequence)
);

CREATE INDEX IF NOT EXISTS ix_control_events_tenant_recorded
  ON control_events (tenant_id, recorded_at);
CREATE INDEX IF NOT EXISTS ix_control_events_action
  ON control_events (action_id);

CREATE TABLE IF NOT EXISTS lineage_nodes (
  node_id UUID PRIMARY KEY,
  run_id UUID NOT NULL,
  tenant_id VARCHAR(255) NOT NULL,
  kind VARCHAR(100) NOT NULL,
  digest CHAR(64) NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_lineage_nodes_run_kind
  ON lineage_nodes (run_id, kind);
CREATE INDEX IF NOT EXISTS ix_lineage_nodes_tenant_recorded
  ON lineage_nodes (tenant_id, recorded_at);

CREATE TABLE IF NOT EXISTS lineage_relations (
  id BIGSERIAL PRIMARY KEY,
  run_id UUID NOT NULL,
  tenant_id VARCHAR(255) NOT NULL,
  relation VARCHAR(100) NOT NULL,
  subject_node_id UUID NOT NULL REFERENCES lineage_nodes(node_id),
  object_node_id UUID NOT NULL REFERENCES lineage_nodes(node_id),
  recorded_at TIMESTAMPTZ NOT NULL,
  document JSONB NOT NULL,
  CONSTRAINT uq_lineage_relations_edge
    UNIQUE (run_id, relation, subject_node_id, object_node_id)
);

CREATE INDEX IF NOT EXISTS ix_lineage_relations_run
  ON lineage_relations (run_id);

CREATE OR REPLACE FUNCTION prodkit_reject_append_only_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS control_events_no_update_delete ON control_events;
CREATE TRIGGER control_events_no_update_delete
BEFORE UPDATE OR DELETE ON control_events
FOR EACH ROW EXECUTE FUNCTION prodkit_reject_append_only_mutation();

DROP TRIGGER IF EXISTS lineage_nodes_no_update_delete ON lineage_nodes;
CREATE TRIGGER lineage_nodes_no_update_delete
BEFORE UPDATE OR DELETE ON lineage_nodes
FOR EACH ROW EXECUTE FUNCTION prodkit_reject_append_only_mutation();

DROP TRIGGER IF EXISTS lineage_relations_no_update_delete ON lineage_relations;
CREATE TRIGGER lineage_relations_no_update_delete
BEFORE UPDATE OR DELETE ON lineage_relations
FOR EACH ROW EXECUTE FUNCTION prodkit_reject_append_only_mutation();

COMMENT ON TABLE control_events IS
  'Canonical append-only ProdKit control event ledger. Corrections use new events.';
COMMENT ON TABLE lineage_nodes IS
  'Immutable content-addressed product identities in the ProdKit lineage graph.';
COMMENT ON TABLE lineage_relations IS
  'Immutable typed relationships between ProdKit lineage identities.';
