from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
EXACT_SHA = re.compile(r"^[a-f0-9]{40}$")
USES = re.compile(r"^\s*uses:\s*([^\s#]+)", re.MULTILINE)


def check_workflow_pins() -> None:
    violations: list[str] = []
    workflow_paths = sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")))
    for path in workflow_paths:
        text = path.read_text(encoding="utf-8")
        for match in USES.finditer(text):
            target = match.group(1).strip("\"'")
            if target.startswith("./"):
                continue
            if "@" not in target:
                violations.append(f"{path.relative_to(ROOT)}: unpinned action {target}")
                continue
            _, ref = target.rsplit("@", 1)
            if not EXACT_SHA.fullmatch(ref):
                violations.append(
                    f"{path.relative_to(ROOT)}: action/reusable workflow ref is not an exact SHA: {target}"
                )
    if violations:
        raise SystemExit("\n".join(violations))


def check_release_contract() -> None:
    manifest_path = ROOT / ".prodkit" / "release.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    build = manifest.get("build")
    if not isinstance(build, dict) or build.get("source_archive") is not True:
        raise SystemExit("release manifest must require a source archive")
    script = build.get("script")
    if not isinstance(script, str) or not script.startswith(".prodkit/workflows/"):
        raise SystemExit("release build script must be repository-controlled")
    for lockfile in (ROOT / "uv.lock", ROOT / "pnpm-lock.yaml"):
        if not lockfile.is_file():
            raise SystemExit(f"required dependency lockfile is missing: {lockfile.name}")


def check_kubernetes_baseline() -> None:
    deployment = (ROOT / "deploy" / "kubernetes" / "base" / "deployment.yaml").read_text(
        encoding="utf-8"
    )
    required = (
        "runAsNonRoot: true",
        "seccompProfile:",
        "type: RuntimeDefault",
        "allowPrivilegeEscalation: false",
        "readOnlyRootFilesystem: true",
        'capabilities: { drop: ["ALL"] }',
        "automountServiceAccountToken: false",
    )
    missing = [fragment for fragment in required if fragment not in deployment]
    if missing:
        raise SystemExit(f"Kubernetes hardening baseline is incomplete: {missing}")
    if ":latest" in deployment:
        raise SystemExit("Kubernetes base must not deploy the mutable :latest image tag")


def main() -> None:
    check_workflow_pins()
    check_release_contract()
    check_kubernetes_baseline()
    print("security policy: workflow pins, release locks, and Kubernetes baseline verified")


if __name__ == "__main__":
    main()
