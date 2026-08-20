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
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
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


def _list_releases(repository: str, token: str) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "page": page})
        status, payload = _request(
            "GET",
            f"https://api.github.com/repos/{repository}/releases?{query}",
            token,
        )
        if status == 404:
            raise RuntimeError("GitHub release listing unexpectedly returned 404")
        if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
            raise RuntimeError("GitHub returned an invalid release-list response")
        releases.extend(payload)
        if len(payload) < 100:
            return releases
        page += 1


def _release_by_tag(repository: str, tag: str, token: str) -> dict[str, Any] | None:
    encoded = urllib.parse.quote(tag, safe="")
    status, payload = _request(
        "GET",
        f"https://api.github.com/repos/{repository}/releases/tags/{encoded}",
        token,
    )
    if status != 404:
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub returned an invalid release response")
        return payload

    # GitHub's release-by-tag endpoint does not reliably expose draft releases.
    # Fall back to the authenticated release listing so an interrupted publication
    # resumes the existing draft instead of trying to create a duplicate.
    matches = [
        release for release in _list_releases(repository, token) if release.get("tag_name") == tag
    ]
    if len(matches) > 1:
        raise RuntimeError(f"GitHub returned multiple release records for tag {tag!r}")
    return matches[0] if matches else None


def _release_by_id(repository: str, release_id: int, token: str) -> dict[str, Any] | None:
    status, payload = _request(
        "GET",
        f"https://api.github.com/repos/{repository}/releases/{release_id}",
        token,
    )
    if status == 404:
        return None
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid release-by-id response")
    return payload


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
    release = _release_by_tag(repository, tag, token)
    if release is None:
        release = _create_draft(repository, tag, token, name=name, body=body)

    if release.get("tag_name") != tag:
        raise RuntimeError("release record does not match the requested tag")
    release_id = release.get("id")
    if not isinstance(release_id, int):
        raise RuntimeError("release response does not contain a numeric id")

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
                    f"existing release asset {path.name!r} has no matching SHA-256 digest"
                )
            continue
        if release.get("draft") is False:
            raise RuntimeError(
                f"published release is missing expected immutable asset {path.name!r}"
            )
        _upload_asset(release, path, token)

    # Refresh by immutable release ID. Draft releases may still be hidden from the
    # tag endpoint at this point even though all uploads succeeded.
    refreshed = _release_by_id(repository, release_id, token)
    if refreshed is None:
        raise RuntimeError("release disappeared after asset upload")
    if refreshed.get("id") != release_id or refreshed.get("tag_name") != tag:
        raise RuntimeError("release identity changed after asset upload")

    refreshed_assets = {
        asset["name"]: asset
        for asset in refreshed.get("assets", [])
        if isinstance(asset, dict) and isinstance(asset.get("name"), str)
    }
    for path in assets:
        remote = refreshed_assets.get(path.name)
        if remote is None or not _asset_matches(remote, path):
            raise RuntimeError(f"release asset SHA-256 verification failed for {path.name!r}")

    if (
        refreshed.get("draft") is not False
        or refreshed.get("prerelease") is not False
        or refreshed.get("name") != name
    ):
        _publish(repository, refreshed, token, name=name, body=body)

    finalized = _release_by_id(repository, release_id, token)
    if finalized is None:
        raise RuntimeError("release disappeared after publication")
    if (
        finalized.get("id") != release_id
        or finalized.get("tag_name") != tag
        or finalized.get("draft") is not False
        or finalized.get("prerelease") is not False
        or finalized.get("name") != name
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
