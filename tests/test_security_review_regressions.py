from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prodkit_control_core import RateLimitPolicy
from prodkit_control_runtime.security import InMemoryReplayStore, SlidingWindowRateLimiter
from scripts import check_security_policy


class _NoItemsClaims(dict[str, datetime]):
    def items(self):  # type: ignore[override]
        raise AssertionError("replay claims must not be scanned")


def test_replay_store_uses_expiration_index_without_scanning_claims() -> None:
    now = datetime.now(UTC)
    store = InMemoryReplayStore(max_entries=10)
    assert store.claim_once(
        key="claim-a",
        expires_at=now + timedelta(seconds=10),
        now=now,
    )
    store._claims = _NoItemsClaims(store._claims)  # type: ignore[assignment]

    assert not store.claim_once(
        key="claim-a",
        expires_at=now + timedelta(seconds=10),
        now=now,
    )

    later = now + timedelta(seconds=11)
    assert store.claim_once(
        key="claim-b",
        expires_at=later + timedelta(seconds=10),
        now=later,
    )
    assert "claim-a" not in store._claims


def test_rate_limit_expiration_index_stays_bounded_across_windows() -> None:
    current = [0.0]
    limiter = SlidingWindowRateLimiter(
        RateLimitPolicy(policy_id="api", limit=3, window_seconds=10, max_keys=10),
        clock=lambda: current[0],
    )

    for step in range(100):
        current[0] = step * 11.0
        assert limiter.check("tenant-a:principal-a").allowed is True
        assert len(limiter._expirations) == 1

    assert len(limiter._entries) == 1


def test_workflow_pin_policy_rejects_flow_style_uses(tmp_path, monkeypatch) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "flow.yaml").write_text(
        "jobs:\n"
        "  check:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - { uses: actions/checkout@v4 }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(check_security_policy, "WORKFLOWS", workflows)
    monkeypatch.setattr(check_security_policy, "ROOT", tmp_path)

    with pytest.raises(SystemExit, match=r"flow\.yaml"):
        check_security_policy.check_workflow_pins()
