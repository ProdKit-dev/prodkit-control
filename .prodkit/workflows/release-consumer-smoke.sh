#!/usr/bin/env bash
set -euo pipefail

artifact_dir="${1:?release artifact directory is required}"
python_version="${2:-${PRODKIT_RELEASE_PYTHON_VERSION:-3.13}}"
artifact_dir="$(realpath "$artifact_dir")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

python3 - "$artifact_dir" <<'PY'
from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

root = Path(sys.argv[1])
artifacts = sorted(path for path in root.iterdir() if path.is_file())
if not artifacts:
    raise SystemExit("release consumer smoke received no artifacts")

for path in artifacts:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        basenames = {Path(name).name for name in names}
        missing = {"LICENSE", "NOTICE"} - basenames
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path, "r:gz") as archive:
            names = archive.getnames()
        basenames = {Path(name).name for name in names}
        missing = {"README.md", "LICENSE", "NOTICE"} - basenames
    elif path.suffix == ".tgz":
        with tarfile.open(path, "r:gz") as archive:
            names = set(archive.getnames())
        missing = {"package/README.md", "package/LICENSE", "package/NOTICE"} - names
    else:
        continue
    if missing:
        raise SystemExit(f"{path.name}: public distribution files missing: {sorted(missing)}")

print("release artifacts contain required public documentation/legal files")
PY

mapfile -t wheels < <(find "$artifact_dir" -maxdepth 1 -type f -name '*.whl' -print | sort)
mapfile -t npm_archives < <(find "$artifact_dir" -maxdepth 1 -type f -name '*.tgz' -print | sort)
if (( ${#wheels[@]} == 0 || ${#npm_archives[@]} == 0 )); then
  echo "release consumer smoke requires Python wheels and npm archives" >&2
  exit 2
fi

uv venv --python "$python_version" "$tmp/venv"
uv pip install --python "$tmp/venv/bin/python" "${wheels[@]}"

pushd "$tmp" >/dev/null
"$tmp/venv/bin/prodkit-control" demo --output "$tmp/demo"
bundle="$(find "$tmp/demo" -maxdepth 1 -type f -name '*.zip' -print -quit)"
test -n "$bundle"
"$tmp/venv/bin/prodkit-control" verify-bundle "$bundle"
"$tmp/venv/bin/python" - <<'PY'
import prodkit_control_core
import prodkit_control_runtime
from prodkit_control_fastapi import create_app

app = create_app()
schema = app.openapi()
assert schema["info"]["title"] == "ProdKit Control API"
assert "/healthz" in schema["paths"]
assert "/readyz" in schema["paths"]
assert prodkit_control_core is not None
assert prodkit_control_runtime is not None
print("clean Python artifact installation and API import smoke passed")
PY

mkdir npm-consumer
cd npm-consumer
npm init -y >/dev/null
npm install --ignore-scripts --no-audit --no-fund "${npm_archives[@]}" >/dev/null
node --input-type=module - <<'JS'
await import("@prodkit/control");
const client = await import("@prodkit/control-client");
const next = await import("@prodkit/control-next");
const react = await import("@prodkit/control-react");
if (typeof client.ControlClient !== "function") throw new Error("control-client export missing");
if (typeof next.ControlServerClient !== "function") throw new Error("control-next export missing");
if (typeof react.ControlResource !== "function") throw new Error("control-react export missing");
console.log("clean npm artifact installation/import smoke passed");
JS
popd >/dev/null

echo "release consumer smoke passed for exact built artifacts"
