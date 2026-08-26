from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _root_version() -> str:
    payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = payload.get("project")
    assert isinstance(project, dict)
    version = project.get("version")
    assert isinstance(version, str)
    return version


def test_kubernetes_base_image_matches_release_distribution_boundary() -> None:
    version = _root_version()
    contract = json.loads((ROOT / ".prodkit/public-readiness.json").read_text(encoding="utf-8"))
    deployment = (ROOT / "deploy/kubernetes/base/deployment.yaml").read_text(encoding="utf-8")

    image_matches = re.findall(r"^\s*image:\s*(\S+)\s*$", deployment, flags=re.MULTILINE)
    assert image_matches == [f"prodkit-control:{version}"]

    distribution = contract.get("distribution")
    assert isinstance(distribution, dict)
    if distribution.get("container_registry") == "not_claimed":
        assert "ghcr.io/prodkit-dev/prodkit-control:" not in deployment
        assert re.search(r"^\s*imagePullPolicy:\s*Never\s*$", deployment, flags=re.MULTILINE)
