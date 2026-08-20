from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast


def _result_location(result: dict[str, Any]) -> str:
    locations = result.get("locations")
    if not isinstance(locations, list) or not locations:
        return "unknown location"
    physical = locations[0].get("physicalLocation")
    if not isinstance(physical, dict):
        return "unknown location"
    artifact = physical.get("artifactLocation")
    region = physical.get("region")
    uri = artifact.get("uri") if isinstance(artifact, dict) else None
    line = region.get("startLine") if isinstance(region, dict) else None
    if isinstance(uri, str) and isinstance(line, int):
        return f"{uri}:{line}"
    if isinstance(uri, str):
        return uri
    return "unknown location"


def check(directory: Path) -> None:
    sarif_files = sorted(directory.rglob("*.sarif"))
    if not sarif_files:
        raise SystemExit(f"no SARIF files found under {directory}")

    findings: list[str] = []
    for path in sarif_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise SystemExit(f"{path}: SARIF root must be an object")
        runs = payload.get("runs")
        if not isinstance(runs, list) or not runs:
            raise SystemExit(f"{path}: SARIF contains no runs")
        for run in runs:
            if not isinstance(run, dict):
                raise SystemExit(f"{path}: SARIF run must be an object")
            results = run.get("results", [])
            if not isinstance(results, list):
                raise SystemExit(f"{path}: SARIF results must be an array")
            for raw_result in results:
                if not isinstance(raw_result, dict):
                    raise SystemExit(f"{path}: SARIF result must be an object")
                result = cast(dict[str, Any], raw_result)
                rule_id = result.get("ruleId", "unknown-rule")
                level = result.get("level", "warning")
                message_raw = result.get("message")
                message = (
                    message_raw.get("text", "")
                    if isinstance(message_raw, dict)
                    else ""
                )
                findings.append(
                    f"{path.name}: {rule_id} [{level}] at {_result_location(result)}: {message}"
                )

    if findings:
        print("CodeQL reported findings:")
        for finding in findings:
            print(f"- {finding}")
        raise SystemExit(f"CodeQL gate failed with {len(findings)} finding(s)")

    print(f"CodeQL gate passed: {len(sarif_files)} SARIF file(s), zero findings")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail a self-hosted CodeQL gate when SARIF contains findings"
    )
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    check(args.directory)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
