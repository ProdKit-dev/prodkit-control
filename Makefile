.PHONY: install check test lint format typecheck typecheck-python typecheck-ts build-ts schemas api demo docker-up docker-down

install:
	uv sync --all-packages --group dev

check: lint typecheck test schemas

lint:
	uv run ruff check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	$(MAKE) typecheck-python
	$(MAKE) typecheck-ts

typecheck-python:
	uv run mypy

typecheck-ts:
	pnpm typecheck:ts

build-ts:
	pnpm build:ts

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
