.PHONY: install check release-check contracts public-readiness smoke test lint format typecheck typecheck-python typecheck-ts build-ts schemas api demo docker-up docker-down

install:
	uv sync --all-packages --group dev --locked
	corepack pnpm install --frozen-lockfile

check: release-check contracts public-readiness lint typecheck test schemas build-ts smoke

release-check:
	uv run python scripts/release_check.py
	uv run python scripts/check_package_completeness.py

contracts:
	uv run python scripts/check_contract_authority.py
	uv run python scripts/check_contract_conformance.py
	corepack pnpm build:ts
	node scripts/check_contract_conformance.mjs
	node scripts/check_control_react.mjs

public-readiness:
	uv run python scripts/check_public_readiness.py

smoke:
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
		uv run prodkit-control demo --output "$$tmp/demo"; \
		uv run python examples/basic_dry_run.py

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	$(MAKE) typecheck-python
	$(MAKE) typecheck-ts

typecheck-python:
	uv run mypy

typecheck-ts:
	corepack pnpm typecheck:ts

build-ts:
	corepack pnpm build:ts

test:
	uv run pytest

schemas:
	uv run python scripts/export_schemas.py --check

api:
	uv run prodkit-control-api

demo:
	uv run prodkit-control demo

docker-up:
	docker compose up --build

docker-down:
	docker compose down -v
