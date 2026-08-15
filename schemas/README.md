# Generated JSON Schemas

These files are generated from the canonical Pydantic contracts:

```bash
uv run python scripts/export_schemas.py
```

CI runs the command with `--check` and fails when committed schemas drift from source contracts.
