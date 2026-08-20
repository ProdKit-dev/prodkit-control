#!/usr/bin/env bash
set -euo pipefail

corepack enable
corepack prepare pnpm@10.15.0 --activate
pnpm install --frozen-lockfile
pnpm audit --audit-level high