#!/usr/bin/env bash
set -euo pipefail

git diff --check
version="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
python3 scripts/release_check.py --version "$version"
python3 scripts/test_release_output_contract.py
