#!/usr/bin/env bash
set -euo pipefail

npm install --global pnpm@10.15.0
pnpm install --frozen-lockfile
pnpm audit --audit-level high
