from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from scripts import publish_release as publisher


def _asset_record(path: Path) -> dict[str, Any]:
    return {
        "name": path.name,
        "size": path.stat().st_size,
        "digest": f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}",
    }


def test_release_by_tag_falls_back_to_authenticated_listing_for_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = {"id": 42, "tag_name": "v1.2.3", "draft": True}

    def fake_request(
        method: str,
        url: str,
        token: str,
        **kwargs: object,
    ) -> tuple[int, Any]:
        if "/releases/tags/v1.2.3" in url:
            return 404, None
        if "/releases?" in url:
            return 200, [draft]
        raise AssertionError(f"unexpected request: {method} {url} {token} {kwargs}")

    monkeypatch.setattr(publisher, "_request", fake_request)

    assert publisher._release_by_tag("ProdKit-dev/prodkit-control", "v1.2.3", "token") == draft


def test_release_by_tag_rejects_ambiguous_duplicate_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate = {"id": 42, "tag_name": "v1.2.3", "draft": True}

    def fake_request(
        method: str,
        url: str,
        token: str,
        **kwargs: object,
    ) -> tuple[int, Any]:
        if "/releases/tags/v1.2.3" in url:
            return 404, None
        if "/releases?" in url:
            return 200, [duplicate, {**duplicate, "id": 43}]
        raise AssertionError(f"unexpected request: {method} {url} {token} {kwargs}")

    monkeypatch.setattr(publisher, "_request", fake_request)

    with pytest.raises(RuntimeError, match="multiple release records"):
        publisher._release_by_tag("ProdKit-dev/prodkit-control", "v1.2.3", "token")


def test_publish_reuses_existing_draft_and_refreshes_by_release_id(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tag = "v1.2.3"
    notes = tmp_path / "notes.md"
    notes.write_text("release notes\n", encoding="utf-8")
    asset = tmp_path / "artifact.bin"
    asset.write_bytes(b"payload")
    asset_record = _asset_record(asset)

    draft = {
        "id": 42,
        "tag_name": tag,
        "name": f"ProdKit Control {tag}",
        "draft": True,
        "prerelease": False,
        "assets": [],
        "upload_url": "https://uploads.github.com/repos/ProdKit-dev/prodkit-control/releases/42/assets{?name,label}",
    }
    refreshed = {**draft, "assets": [asset_record]}
    finalized = {
        **refreshed,
        "draft": False,
        "prerelease": False,
    }
    id_reads = iter([refreshed, finalized])
    uploaded: list[str] = []
    published: list[int] = []

    monkeypatch.setenv("GITHUB_REPOSITORY", "ProdKit-dev/prodkit-control")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(publisher, "_release_by_tag", lambda *args: draft)
    monkeypatch.setattr(
        publisher,
        "_create_draft",
        lambda *args, **kwargs: pytest.fail("existing draft must be reused"),
    )
    monkeypatch.setattr(
        publisher,
        "_upload_asset",
        lambda release, path, token: uploaded.append(path.name),
    )
    monkeypatch.setattr(
        publisher,
        "_release_by_id",
        lambda repository, release_id, token: next(id_reads),
    )

    def fake_publish(
        repository: str,
        release: dict[str, Any],
        token: str,
        *,
        name: str,
        body: str,
    ) -> dict[str, Any]:
        assert repository == "ProdKit-dev/prodkit-control"
        assert token == "token"
        assert name == f"ProdKit Control {tag}"
        assert body == "release notes\n"
        published.append(release["id"])
        return finalized

    monkeypatch.setattr(publisher, "_publish", fake_publish)

    publisher.publish(tag, notes, [asset])

    assert uploaded == [asset.name]
    assert published == [42]
