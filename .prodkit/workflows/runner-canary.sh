#!/usr/bin/env bash
set -euo pipefail

git diff --check
python3 --version
uname -a

echo "runner canary passed on ${RUNNER_NAME:-unknown} (${RUNNER_OS:-unknown}/${RUNNER_ARCH:-unknown})"
