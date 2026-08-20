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
RELEASE_NAME_PREFIX = "ProdKit Control"
_ALLOWED_GITHUB_HOSTS = frozenset({"api.github.com", "uploads.github.com"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_name(tag: str) -> str:
    return f"{RELEASE_NAME_PREFIX} {tag}"


def _validate_github_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_GITHUB_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RuntimeError("release request URL is outside the allowed GitHub HTTPS origins")


def _request(
    method: str,
    url: str,
    token: str,
    *,
    json_body: dict[str, Any] | None = None,
    data: bytes | None = None,
    content_type: str | None = None,
) -> tuple[int, Any]:
    _validate_github_url(url)
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
    request = urllib.request.Request(  # noqa: S310 - URL is allow-listed above.
        url, data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
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
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        return False
    return digest == f"sha256:{_sha256(path)}"


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


def _release_by_id(repository: str, release_id: int, token: str) -> dict[str, Any]:
    status, payload = _request(
        "GET",
        f"https://api.github.com/repos/{repository}/releases/{release_id}",
        token,
    )
    if status == 404 or not isinstance(payload, dict):
        raise RuntimeError(f"GitHub Release {release_id} could not be read")
    return payload


def _find_release(repository: str, tag: str, token: str) -> dict[str, Any] | None:
    published = _release_by_tag(repository, tag, token)
    if published is not None:
        return published
    status, payload = _request(
        "GET",
        f"https://api.github.com/repos/{repository}/releases?per_page=100",
        token,
    )
    if status == 404:
        return None
    if not isinstance(payload, list):
        raise RuntimeError("GitHub returned an invalid release listing")
    matches = [item for item in payload if isinstance(item, dict) and item.get("tag_name") == tag]
    if len(matches) > 1:
        raise RuntimeError(f"multiple GitHub Releases exist for {tag}")
    return matches[0] if matches else None


def _create_draft(
    repository: str,
    tag: str,
    token: str,
    *,
    name: str,
    body: str,
) -> dict[str, Any]:
    _, payload = _request(
        "POST",
        f"https://api.github.com/repos/{repository}/releases",
        token,
        json_body={
            "tag_name": tag,
            "name": name,
            "body": body,
            "draft": True,
            "prerelease": False,
            "generate_release_notes": False,
        },
    )
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid release creation response")
    return payload


def _delete_asset(repository: str, asset: dict[str, Any], token: str) -> None:
    asset_id = asset.get("id")
    if not isinstance(asset_id, int):
        raise RuntimeError("release asset does not contain a numeric id")
    status, _ = _request(
        "DELETE",
        f"https://api.github.com/repos/{repository}/releases/assets/{asset_id}",
        token,
    )
    if status not in {204, 404}:
        raise RuntimeError(f"GitHub returned unexpected asset deletion status {status}")


def _upload_asset(release: dict[str, Any], path: Path, token: str) -> None:
    upload_url = release.get("upload_url")
    if not isinstance(upload_url, str):
        raise RuntimeError("release response does not contain upload_url")
    base = upload_url.split("{", 1)[0]
    url = f"{base}?{urllib.parse.urlencode({'name': path.name})}"
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    _request("POST", url, token, data=path.read_bytes(), content_type=content_type)


def _publish(
    repository: str,
    release: dict[str, Any],
    token: str,
    *,
    name: str,
    body: str,
) -> dict[str, Any]:
    release_id = release.get("id")
    if not isinstance(release_id, int):
        raise RuntimeError("release response does not contain a numeric id")
    _, payload = _request(
        "PATCH",
        f"https://api.github.com/repos/{repository}/releases/{release_id}",
        token,
        json_body={
            "name": name,
            "draft": False,
            "prerelease": False,
            "body": body,
        },
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
    name = _release_name(tag)
    release = _find_release(repository, tag, token)
    if release is None:
        release = _create_draft(repository, tag, token, name=name, body=body)
    if release.get("tag_name") != tag:
        raise RuntimeError("GitHub Release tag does not match requested tag")

    release_id = release.get("id")
    if not isinstance(release_id, int):
        raise RuntimeError("release response does not contain a numeric id")

    expected = {path.name: path for path in assets}
    remote_assets = {
        asset["name"]: asset
        for asset in release.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }

    if release.get("draft") is False:
        if set(remote_assets) != set(expected):
            raise RuntimeError("published release asset set is not the expected immutable set")
        for filename, path in expected.items():
            if not _asset_matches(remote_assets[filename], path):
                raise RuntimeError(f"published release asset digest mismatch for {filename!r}")
    else:
        for filename, asset in list(remote_assets.items()):
            path = expected.get(filename)
            if path is None or not _asset_matches(asset, path):
                _delete_asset(repository, asset, token)
                remote_assets.pop(filename, None)
        release = _release_by_id(repository, release_id, token)
        remote_assets = {
            asset["name"]: asset
            for asset in release.get("assets", [])
            if isinstance(asset, dict) and isinstance(asset.get("name"), str)
        }
        for filename, path in expected.items():
            if filename not in remote_assets:
                _upload_asset(release, path, token)

        release = _release_by_id(repository, release_id, token)
        refreshed_assets = {
            asset["name"]: asset
            for asset in release.get("assets", [])
            if isinstance(asset, dict) and isinstance(asset.get("name"), str)
        }
        if set(refreshed_assets) != set(expected):
            raise RuntimeError("draft release asset set does not exactly match expected assets")
        for filename, path in expected.items():
            if not _asset_matches(refreshed_assets[filename], path):
                raise RuntimeError(f"release asset SHA-256 verification failed for {filename!r}")
        release = _publish(repository, release, token, name=name, body=body)

    if (
        release.get("draft") is not False
        or release.get("prerelease") is not False
        or release.get("name") != name
        or release.get("tag_name") != tag
    ):
        release = _publish(repository, release, token, name=name, body=body)

    final_release = _release_by_tag(repository, tag, token)
    if final_release is None:
        raise RuntimeError("published release cannot be resolved by immutable tag")
    final_assets = {
        asset["name"]: asset
        for asset in final_release.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    if set(final_assets) != set(expected):
        raise RuntimeError("final published release asset set is not exact")
    for filename, path in expected.items():
        if not _asset_matches(final_assets[filename], path):
            raise RuntimeError(f"final published release digest mismatch for {filename!r}")
    if (
        final_release.get("draft") is not False
        or final_release.get("prerelease") is not False
        or final_release.get("name") != name
        or final_release.get("tag_name") != tag
    ):
        raise RuntimeError("release did not reach the required final published metadata state")
    print(f"Published {name} with {len(assets)} SHA-256-verified assets")


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
