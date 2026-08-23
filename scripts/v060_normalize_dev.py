from __future__ import annotations

from pathlib import Path


path = Path("scripts/ci_governance_migrations.py")
text = path.read_text(encoding="utf-8")
old = '''        if preserved is None or preserved["tenant_id"] != tenant_id or preserved["status"] != "running":
            raise AssertionError(f"upgrade from {start_version} did not preserve run ownership/state")
        if preserved["document"]["run_id"] != str(run_id):
            raise AssertionError(f"upgrade from {start_version} modified canonical run document")
'''
new = '''        if preserved is None or preserved["tenant_id"] != tenant_id or preserved["status"] != "running":
            raise AssertionError(f"upgrade from {start_version} did not preserve run ownership/state")
        preserved_document = preserved["document"]
        if isinstance(preserved_document, str):
            preserved_document = json.loads(preserved_document)
        if not isinstance(preserved_document, dict) or preserved_document.get("run_id") != str(run_id):
            raise AssertionError(f"upgrade from {start_version} modified canonical run document")
'''
if old not in text:
    raise SystemExit("expected migration preservation block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
