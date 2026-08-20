from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_VERSION = "2022-11-28"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request(
    method: str,
    url: str,
    token: str,
    *,
    json_body: dict[str, Any] | None = None,
    data: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, Any]:
    body = data
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "prodkit-control-release",
    }
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif content_type is not None:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
            decoded = json.loads(payload) if payload else None
            return response.status, decoded
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404:
            return 404, None
        raise RuntimeError(f"GitHub API {method} {url} failed ({exc.code}): {payload}") from exc


def _asset_matches(asset: dict[str, Any], path: Path) -> bool:
    if asset.get("size") != path.stat().st_size:
        return False
    digest = asset.get("digest")
    if isinstance(digest, str) and digest.startswith("sha256:"):
        return digest == f"sha256:{_sha256(path)}"
    return True


def _release_by_tag(repository: str, tag: str, token: str) -> dict[str, Any] | None:
    encoded = urllib.parse.quote(tag, safe="")
    status, payload = _request(
        "GET",
        f"https://api.github.com/repos/{repository}/releases/tags/{encoded}",
        token,
    )
    if status == 404:
        return None
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid release response")
    return payload


def _create_draft(
    repository: str,
    tag: str,
    token: str,
    *,
    body: str,
) -> dict[str, Any]:
    _, payload = _request(
        "POST",
        f"https://api.github.com/repos/{repository}/releases",
        token,
        json_body={
            "tag_name": tag,
            "name": tag,
            "body": body,
            "draft": True,
            "prerelease": False,
            "generate_release_notes": False,
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid release creation response")
    return payload


def _upload_asset(release: dict[str, Any], path: Path, token: str) -> None:
    upload_url = release.get("upload_url")
    if not isinstance(upload_url, str):
        raise RuntimeError("release response does not contain upload_url")
    base = upload_url.split("{", 1)[0]
    url = f"{base}?{urllib.parse.urlencode({'name': path.name})}"
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    _request("POST", url, token, data=path.read_bytes(), content_type=content_type)


def _publish(repository: str, release: dict[str, Any], token: str, body: str) -> dict[str, Any]:
    release_id = release.get("id")
    if not isinstance(release_id, int):
        raise RuntimeError("release response does not contain a numeric id")
    _, payload = _request(
        "PATCH",
        f"https://api.github.com/repos/{repository}/releases/{release_id}",
        token,
        json_body={"draft": False, "prerelease": False, "body": body},
    )
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid release publish response")
    return payload


def publish(tag: str, notes: Path, assets: list[Path]) -> None:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repository or "/" not in repository:
        raise RuntimeError("GITHUB_REPOSITORY must be owner/name")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    if not assets:
        raise RuntimeError("at least one release asset is required")
    for path in assets:
        if not path.is_file():
            raise FileNotFoundError(path)
    names = [path.name for path in assets]
    if len(names) != len(set(names)):
        raise RuntimeError("release asset filenames must be unique")

    body = notes.read_text(encoding="utf-8")
    release = _release_by_tag(repository, tag, token)
    if release is None:
        release = _create_draft(repository, tag, token, body=body)

    remote_assets = {
        asset["name"]: asset
        for asset in release.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    for path in assets:
        existing = remote_assets.get(path.name)
        if existing is not None:
            if not _asset_matches(existing, path):
                raise RuntimeError(
                    f"existing release asset {path.name!r} differs from the local artifact"
                )
            continue
        if release.get("draft") is False:
            raise RuntimeError(
                f"published release is missing expected immutable asset {path.name!r}"
            )
        _upload_asset(release, path, token)

    refreshed = _release_by_tag(repository, tag, token)
    if refreshed is None:
        raise RuntimeError("release disappeared after asset upload")
    refreshed_assets = {
        asset["name"]: asset
        for asset in refreshed.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    for path in assets:
        remote = refreshed_assets.get(path.name)
        if remote is None or not _asset_matches(remote, path):
            raise RuntimeError(f"release asset verification failed for {path.name!r}")

    if refreshed.get("draft") is not False:
        refreshed = _publish(repository, refreshed, token, body)
    if refreshed.get("draft") is not False or refreshed.get("prerelease") is not False:
        raise RuntimeError("release did not reach final published state")
    print(f"Published {tag} with {len(assets)} verified assets")


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish an immutable GitHub release")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("assets", nargs="+", type=Path)
    args = parser.parse_args()
    publish(args.tag, args.notes, args.assets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
