from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from prodkit_agentgateway import AgentFrameworkActionAdapter, AgentToolBinding, AgentToolInvocation
from prodkit_control_core import (
    EffectClass,
    IntegrityViolationError,
    RiskClass,
    SLSA_PROVENANCE_V1,
    SlsaProvenancePredicateV1,
    TrustRootPolicy,
)
from prodkit_control_runtime import (
    Ed25519CheckpointSigner,
    OfflineAssuranceVerifier,
    PortableAttestationBuilder,
)


def test_slsa_provenance_predicate_has_a_validation_path() -> None:
    builder = PortableAttestationBuilder()
    statement = builder.slsa_build_provenance_statement(
        subjects=(builder.resource(name="artifact", sha256="a" * 64),),
        build_type="https://example.test/build/v1",
        builder_id="https://example.test/builder",
        external_parameters={"ref": "refs/tags/v0.3.0"},
    )
    assert statement.predicate_type == SLSA_PROVENANCE_V1
    parsed = SlsaProvenancePredicateV1.model_validate(statement.predicate)
    assert parsed.build_definition.build_type == "https://example.test/build/v1"
    assert parsed.run_details.builder.id == "https://example.test/builder"


def test_framework_neutral_agent_adapter_only_proposes_admin_bound_actions() -> None:
    adapter = AgentFrameworkActionAdapter(
        (
            AgentToolBinding(
                tool_name="deploy",
                executor="deployment",
                operation="deploy",
                effect_class=EffectClass.WRITE,
                risk_class=RiskClass.HIGH,
                target_system="runtime",
                target_environment="production",
                target_resource_type="service",
                target_resource_id_argument="service",
            ),
        )
    )
    invocation = AgentToolInvocation(
        framework="custom-agent",
        session_id="session-1",
        call_id="call-1",
        tool_name="deploy",
        arguments={"service": "api", "image": "sha256:deadbeef"},
    )
    action = adapter.propose(
        invocation,
        run_id=uuid4(),
        tenant_id="tenant-a",
        proposed_at=datetime.now(UTC),
    )
    assert action.executor == "deployment"
    assert action.effect_class is EffectClass.WRITE
    assert action.risk_class is RiskClass.HIGH
    assert action.target.resource_id == "api"
    assert action.policy_context["interop.framework"] == "custom-agent"

    with pytest.raises(ValueError, match="unbound agent tool"):
        adapter.propose(
            AgentToolInvocation(
                framework="custom-agent",
                session_id="session-1",
                call_id="call-2",
                tool_name="delete-everything",
                arguments={"service": "api"},
            ),
            run_id=uuid4(),
            tenant_id="tenant-a",
            proposed_at=datetime.now(UTC),
        )


def test_two_key_rotation_accepts_each_key_only_inside_its_trust_window() -> None:
    now = datetime.now(UTC)
    old_signer = Ed25519CheckpointSigner.generate(key_id="release-old", signer_id="release-service")
    new_signer = Ed25519CheckpointSigner.generate(key_id="release-new", signer_id="release-service")
    rotation = now + timedelta(hours=1)
    trust = TrustRootPolicy(
        policy_id="release-rotation",
        revision="2026-q3",
        trusted_keys=(
            old_signer.trusted_key(
                valid_from=now - timedelta(days=30),
                valid_until=rotation,
            ),
            new_signer.trusted_key(
                valid_from=rotation,
                valid_until=rotation + timedelta(days=90),
            ),
        ),
        allowed_signers=("release-service",),
    )
    run_id = uuid4()
    old_checkpoint = old_signer.sign(
        run_id=run_id,
        tenant_id="tenant-a",
        created_at=now,
        sequence=1,
        final_event_hash="a" * 64,
        evidence_bundle_sha256="b" * 64,
    )
    new_checkpoint = new_signer.sign(
        run_id=run_id,
        tenant_id="tenant-a",
        created_at=rotation + timedelta(seconds=1),
        sequence=2,
        final_event_hash="a" * 64,
        evidence_bundle_sha256="b" * 64,
    )
    assert OfflineAssuranceVerifier.verify_checkpoint(old_checkpoint, trust_policy=trust).key_id == (
        "release-old"
    )
    assert OfflineAssuranceVerifier.verify_checkpoint(new_checkpoint, trust_policy=trust).key_id == (
        "release-new"
    )

    too_early_new = new_signer.sign(
        run_id=run_id,
        tenant_id="tenant-a",
        created_at=now,
        sequence=3,
        final_event_hash="a" * 64,
        evidence_bundle_sha256="b" * 64,
    )
    with pytest.raises(IntegrityViolationError, match="predates signing-key validity"):
        OfflineAssuranceVerifier.verify_checkpoint(too_early_new, trust_policy=trust)
