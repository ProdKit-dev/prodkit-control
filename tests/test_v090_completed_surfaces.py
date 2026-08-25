from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from prodkit_control_core import ActionSpec, ActionTarget, EffectClass, RiskClass, sha256_hex
from prodkit_e2b.sandbox import E2BSandboxAdapter, E2BSandboxConfig, SandboxExecution
from prodkit_executor_database.executor import ConstrainedDatabaseExecutor, DatabaseExecutorConfig
from prodkit_executor_deployment.executor import (
    ConstrainedDeploymentExecutor,
    DeploymentExecutorConfig,
    DeploymentReceipt,
)
from prodkit_executor_kubernetes.executor import (
    ConstrainedKubernetesExecutor,
    KubernetesExecutorConfig,
)
from prodkit_provider_openai.provider import OpenAIProvider


class DummyResolver:
    async def resolve(self, lease: object) -> dict[str, str]:
        del lease
        return {"token": "test", "dsn": "postgresql://example.invalid/test"}


class DummyDeploymentTransport:
    async def perform(self, **kwargs: Any) -> DeploymentReceipt:
        del kwargs
        return DeploymentReceipt(operation_id="op-1", state={"status": "ready"})


class DummyE2BClient:
    async def run(self, **kwargs: Any) -> SandboxExecution:
        del kwargs
        return SandboxExecution(
            sandbox_id="sandbox-1",
            exit_code=0,
            stdout=b"ok",
            stderr=b"",
        )


def action(
    *,
    executor: str,
    operation: str,
    resource_type: str,
    resource_id: str,
    arguments: dict[str, object],
    effect_class: EffectClass = EffectClass.WRITE,
    environment: str = "production",
) -> ActionSpec:
    return ActionSpec(
        action_id=uuid4(),
        run_id=uuid4(),
        tenant_id="tenant-a",
        executor=executor,
        operation=operation,
        effect_class=effect_class,
        risk_class=RiskClass.HIGH,
        target=ActionTarget(
            system=executor,
            environment=environment,
            resource_type=resource_type,
            resource_id=resource_id,
        ),
        arguments=arguments,
        idempotency_key=f"test:{uuid4()}",
        proposed_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_database_executor_fails_closed_on_statement_and_lease() -> None:
    statement = "SELECT id FROM accounts WHERE id = $1"
    executor = ConstrainedDatabaseExecutor(
        DatabaseExecutorConfig(
            allowed_databases=frozenset({"accounts"}),
            allowed_statement_sha256=frozenset({sha256_hex(statement)}),
        ),
        credential_resolver=DummyResolver(),
    )
    allowed = action(
        executor="database",
        operation="query",
        resource_type="database",
        resource_id="accounts",
        arguments={"statement": statement, "parameters": ["123"]},
        effect_class=EffectClass.READ,
    )
    await executor.validate(allowed)
    with pytest.raises(PermissionError, match="short-lived credential lease"):
        await executor.execute(allowed)

    denied = allowed.model_copy(update={"arguments": {"statement": "SELECT * FROM secrets"}})
    with pytest.raises(PermissionError, match="statement is not allowlisted"):
        await executor.validate(denied)


@pytest.mark.asyncio
async def test_deployment_executor_requires_immutable_artifact_and_allowlisted_target() -> None:
    executor = ConstrainedDeploymentExecutor(
        DeploymentExecutorConfig(
            allowed_environments=frozenset({"production"}),
            allowed_resources=frozenset({"api"}),
        ),
        credential_resolver=DummyResolver(),
        transport=DummyDeploymentTransport(),
    )
    immutable = action(
        executor="deployment",
        operation="deploy",
        resource_type="service",
        resource_id="api",
        arguments={"artifact_digest": "sha256:" + "a" * 64},
    )
    await executor.validate(immutable)

    mutable = immutable.model_copy(update={"arguments": {"artifact_digest": "latest"}})
    with pytest.raises(ValueError, match="sha256"):
        await executor.validate(mutable)

    foreign = immutable.model_copy(
        update={
            "target": immutable.target.model_copy(update={"resource_id": "unapproved-service"})
        }
    )
    with pytest.raises(PermissionError, match="resource is not allowlisted"):
        await executor.validate(foreign)


@pytest.mark.asyncio
async def test_kubernetes_executor_requires_immutable_image_and_namespace_allowlist() -> None:
    executor = ConstrainedKubernetesExecutor(
        KubernetesExecutorConfig(
            api_base_url="https://kubernetes.example.invalid",
            allowed_namespaces=frozenset({"prod"}),
            allowed_deployments=frozenset({"api"}),
        ),
        credential_resolver=DummyResolver(),
    )
    immutable = action(
        executor="kubernetes",
        operation="set_image",
        resource_type="deployment",
        resource_id="api",
        arguments={
            "namespace": "prod",
            "container": "api",
            "image": "registry.example.invalid/api@sha256:" + "b" * 64,
        },
    )
    await executor.validate(immutable)

    mutable = immutable.model_copy(
        update={
            "arguments": {
                "namespace": "prod",
                "container": "api",
                "image": "registry.example.invalid/api:latest",
            }
        }
    )
    with pytest.raises(ValueError, match="immutable image"):
        await executor.validate(mutable)

    foreign_namespace = immutable.model_copy(
        update={"arguments": {**immutable.arguments, "namespace": "other"}}
    )
    with pytest.raises(PermissionError, match="namespace is not allowlisted"):
        await executor.validate(foreign_namespace)


@pytest.mark.asyncio
async def test_e2b_adapter_emits_hash_only_output_evidence() -> None:
    command = ("python", "-c", "print('ok')")
    adapter = E2BSandboxAdapter(
        E2BSandboxConfig(
            allowed_templates=frozenset({"python"}),
            allowed_command_sha256=frozenset({sha256_hex(command)}),
        ),
        client=DummyE2BClient(),
    )
    evidence = await adapter.run(template="python", command=command)
    assert evidence.succeeded is True
    assert evidence.stdout_sha256 == sha256_hex(b"ok")
    assert not hasattr(evidence, "stdout")

    with pytest.raises(PermissionError, match="command is not allowlisted"):
        await adapter.run(template="python", command=("sh", "-c", "id"))


def test_openai_adapter_normalizes_tool_calls_and_usage() -> None:
    proposals = OpenAIProvider._tool_proposals(
        [
            {
                "id": "call-1",
                "function": {"name": "deploy", "arguments": '{"service":"api"}'},
            }
        ]
    )
    assert len(proposals) == 1
    assert proposals[0].tool_name == "deploy"
    assert proposals[0].arguments == {"service": "api"}
    assert OpenAIProvider._usage(
        {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}
    ) == {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}
