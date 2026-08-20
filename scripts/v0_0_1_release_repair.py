from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

API_VERSION = "2022-11-28"
TAG = "v0.0.1"
RELEASE_NAME = "ProdKit Control v0.0.1"
EXPECTED_RELEASE_SHA = "889a754f416bd9454bc9e7d407a6f3d0327241d8"
PERMANENT_FIX_SHA = "d3649917ad7e0e115e5860d1ef2da8769e1e49f5"
RELEASE_PR = 6
REPAIR_BRANCH = "release-repair/v0.0.1"
EXPECTED_PAYLOAD_ASSETS = 68
_ALLOWED_HOSTS = {"api.github.com", "uploads.github.com"}
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def validate_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise RuntimeError("GitHub request URL is outside the allowed HTTPS origins")


def request(
    method: str,
    url: str,
    token: str,
    *,
    payload: dict[str, Any] | None = None,
    raw: bytes | None = None,
    content_type: str | None = None,
    accept: str = "application/vnd.github+json",
    allow_404: bool = False,
) -> tuple[int, bytes] | None:
    validate_url(url)
    headers = {
        "Accept": accept,
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "prodkit-control-v0.0.1-release-repair",
    }
    if payload is not None and raw is not None:
        raise RuntimeError("request cannot contain JSON and raw payloads together")
    data = raw
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw is not None:
        headers["Content-Type"] = content_type or "application/octet-stream"
    req = urllib.request.Request(  # noqa: S310 - URL is allow-listed above.
        url, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:  # noqa: S310
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if allow_404 and exc.code == 404:
            return None
        raise RuntimeError(f"GitHub API {method} {url} failed ({exc.code}): {body}") from exc


def json_request(
    method: str,
    url: str,
    token: str,
    *,
    payload: dict[str, Any] | None = None,
    allow_404: bool = False,
) -> Any:
    result = request(method, url, token, payload=payload, allow_404=allow_404)
    if result is None:
        return None
    _, body = result
    return json.loads(body) if body else None


def resolve_tag_commit(api: str, token: str) -> str:
    encoded_tag = urllib.parse.quote(TAG, safe="")
    ref = json_request("GET", f"{api}/git/ref/tags/{encoded_tag}", token)
    if not isinstance(ref, dict) or not isinstance(ref.get("object"), dict):
        raise RuntimeError("release tag reference is malformed")
    obj = ref["object"]
    target_sha = obj.get("sha")
    target_type = obj.get("type")
    if target_type == "tag":
        tag_object = json_request("GET", f"{api}/git/tags/{target_sha}", token)
        if not isinstance(tag_object, dict) or not isinstance(tag_object.get("object"), dict):
            raise RuntimeError("annotated release tag object is malformed")
        target = tag_object["object"]
        if target.get("type") != "commit":
            raise RuntimeError("annotated release tag does not point directly to a commit")
        target_sha = target.get("sha")
    elif target_type != "commit":
        raise RuntimeError(f"unsupported release tag target type: {target_type!r}")
    if not isinstance(target_sha, str):
        raise RuntimeError("release tag has no commit SHA")
    return target_sha


def list_tag_releases(api: str, token: str) -> list[dict[str, Any]]:
    payload = json_request("GET", f"{api}/releases?per_page=100", token)
    if not isinstance(payload, list):
        raise RuntimeError("GitHub returned an invalid release listing")
    return [
        item
        for item in payload
        if isinstance(item, dict) and item.get("tag_name") == TAG
    ]


def download(asset: dict[str, Any], token: str) -> bytes:
    url = asset.get("url")
    if not isinstance(url, str):
        raise RuntimeError("release asset has no API URL")
    result = request("GET", url, token, accept="application/octet-stream")
    if result is None:
        raise RuntimeError("release asset unexpectedly disappeared")
    return result[1]


def verify_github_digest(asset: dict[str, Any], data: bytes) -> str:
    digest = asset.get("digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise RuntimeError(f"asset {asset.get('name')!r} has no GitHub SHA-256 digest metadata")
    expected = digest.removeprefix("sha256:").lower()
    actual = hashlib.sha256(data).hexdigest()
    if expected != actual:
        raise RuntimeError(f"GitHub SHA-256 metadata mismatch for {asset.get('name')!r}")
    return actual


def parse_checksums(sums_bytes: bytes) -> dict[str, str]:
    checksums: dict[str, str] = {}
    for raw in sums_bytes.decode("utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise RuntimeError(f"malformed SHA256SUMS line: {raw!r}")
        digest, name = parts
        name = name.lstrip("*")
        if len(digest) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in digest):
            raise RuntimeError(f"malformed SHA-256 digest for {name!r}")
        if "/" in name or name in {".", "..", "SHA256SUMS"}:
            raise RuntimeError(f"unsafe checksum asset name: {name!r}")
        if name in checksums:
            raise RuntimeError(f"duplicate checksum entry: {name}")
        checksums[name] = digest.lower()
    return checksums


def verify_remote_release_assets(
    api: str, token: str, release: dict[str, Any]
) -> tuple[list[dict[str, Any]], set[str]]:
    release_id = release.get("id")
    if not isinstance(release_id, int):
        raise RuntimeError("release has no numeric id")
    assets = json_request("GET", f"{api}/releases/{release_id}/assets?per_page=100", token)
    if not isinstance(assets, list):
        raise RuntimeError(f"release {release_id} returned malformed assets")
    if len(assets) != EXPECTED_PAYLOAD_ASSETS + 1:
        raise RuntimeError(
            f"release {release_id} has {len(assets)} assets; expected {EXPECTED_PAYLOAD_ASSETS + 1}"
        )

    by_name: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise RuntimeError(f"release {release_id} contains malformed asset metadata")
        name = asset["name"]
        if name in by_name:
            raise RuntimeError(f"release {release_id} has duplicate asset {name!r}")
        by_name[name] = asset
    if "SHA256SUMS" not in by_name:
        raise RuntimeError(f"release {release_id} is missing SHA256SUMS")

    sums_bytes = download(by_name["SHA256SUMS"], token)
    verify_github_digest(by_name["SHA256SUMS"], sums_bytes)
    checksums = parse_checksums(sums_bytes)
    payload_names = set(by_name) - {"SHA256SUMS"}
    if len(payload_names) != EXPECTED_PAYLOAD_ASSETS:
        raise RuntimeError(
            f"release {release_id} has {len(payload_names)} payload assets; "
            f"expected {EXPECTED_PAYLOAD_ASSETS}"
        )
    if set(checksums) != payload_names:
        raise RuntimeError(f"release {release_id} asset set does not match SHA256SUMS")

    for name in sorted(payload_names):
        data = download(by_name[name], token)
        actual = verify_github_digest(by_name[name], data)
        if actual != checksums[name]:
            raise RuntimeError(f"release {release_id} SHA256SUMS mismatch for {name}")
    return assets, payload_names


def verify_local_assets(asset_dir: Path) -> list[Path]:
    if not asset_dir.is_dir():
        raise RuntimeError(f"local release asset directory does not exist: {asset_dir}")
    assets = sorted(path for path in asset_dir.iterdir() if path.is_file())
    if len(assets) != EXPECTED_PAYLOAD_ASSETS + 1:
        raise RuntimeError(
            f"local release directory has {len(assets)} files; expected {EXPECTED_PAYLOAD_ASSETS + 1}"
        )
    by_name = {path.name: path for path in assets}
    if len(by_name) != len(assets) or "SHA256SUMS" not in by_name:
        raise RuntimeError("local release asset names are duplicate or SHA256SUMS is absent")
    checksums = parse_checksums(by_name["SHA256SUMS"].read_bytes())
    payload_names = set(by_name) - {"SHA256SUMS"}
    if len(payload_names) != EXPECTED_PAYLOAD_ASSETS or set(checksums) != payload_names:
        raise RuntimeError("local release payload set does not exactly match SHA256SUMS")
    for name, expected in checksums.items():
        actual = hashlib.sha256(by_name[name].read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"local release checksum mismatch for {name}")
    return assets


def choose_canonical_release(
    api: str, token: str, matches: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, dict[int, str]]:
    verified: list[dict[str, Any]] = []
    failures: dict[int, str] = {}
    for release in matches:
        release_id = release.get("id")
        if not isinstance(release_id, int):
            continue
        try:
            verify_remote_release_assets(api, token, release)
        except RuntimeError as exc:
            failures[release_id] = str(exc)
        else:
            verified.append(release)
    if not verified:
        return None, failures
    verified.sort(key=lambda item: int(item["id"]))
    return verified[0], failures


def delete_asset(api: str, token: str, asset: dict[str, Any]) -> None:
    asset_id = asset.get("id")
    if not isinstance(asset_id, int):
        raise RuntimeError("release asset has no numeric id")
    request("DELETE", f"{api}/releases/assets/{asset_id}", token)


def upload_asset(release: dict[str, Any], path: Path, token: str) -> None:
    upload_url = release.get("upload_url")
    if not isinstance(upload_url, str):
        raise RuntimeError("draft release has no upload_url")
    base_url = upload_url.split("{", 1)[0]
    query = urllib.parse.urlencode({"name": path.name})
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    result = request(
        "POST",
        f"{base_url}?{query}",
        token,
        raw=path.read_bytes(),
        content_type=content_type,
    )
    if result is None or result[0] != 201:
        raise RuntimeError(f"failed to upload release asset {path.name}")


def rebuild_draft_from_local(
    api: str,
    token: str,
    release: dict[str, Any],
    local_assets: list[Path],
) -> dict[str, Any]:
    release_id = release.get("id")
    if not isinstance(release_id, int):
        raise RuntimeError("repair candidate has no numeric id")
    if release.get("draft") is not True:
        raise RuntimeError("refusing to replace assets on a non-draft release")
    remote_assets = json_request("GET", f"{api}/releases/{release_id}/assets?per_page=100", token)
    if not isinstance(remote_assets, list):
        raise RuntimeError("repair candidate returned malformed assets")
    for asset in remote_assets:
        if not isinstance(asset, dict):
            raise RuntimeError("repair candidate has malformed asset metadata")
        delete_asset(api, token, asset)
    refreshed = json_request("GET", f"{api}/releases/{release_id}", token)
    if not isinstance(refreshed, dict):
        raise RuntimeError("repair candidate disappeared before asset upload")
    for path in local_assets:
        upload_asset(refreshed, path, token)
    refreshed = json_request("GET", f"{api}/releases/{release_id}", token)
    if not isinstance(refreshed, dict):
        raise RuntimeError("repair candidate disappeared after asset upload")
    verify_remote_release_assets(api, token, refreshed)
    return refreshed


def delete_branch(api: str, token: str, branch: str) -> str:
    encoded = urllib.parse.quote(branch, safe="")
    result = request("DELETE", f"{api}/git/refs/heads/{encoded}", token, allow_404=True)
    return "already absent" if result is None else "deleted"


def delete_duplicate_release(api: str, token: str, release: dict[str, Any]) -> int:
    release_id = release.get("id")
    if not isinstance(release_id, int):
        raise RuntimeError("duplicate release has no numeric id")
    if release.get("tag_name") != TAG:
        raise RuntimeError("refusing to delete a release for a different tag")
    request("DELETE", f"{api}/releases/{release_id}", token)
    return release_id


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    asset_dir = Path(os.environ.get("RELEASE_ASSET_DIR", "release-source/.release/assets"))
    if not _REPOSITORY_RE.fullmatch(repo):
        raise RuntimeError("GITHUB_REPOSITORY must be a safe owner/name value")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    api = f"https://api.github.com/repos/{repo}"

    target_sha = resolve_tag_commit(api, token)
    if target_sha != EXPECTED_RELEASE_SHA:
        raise RuntimeError(
            f"immutable tag {TAG} points at {target_sha}, expected {EXPECTED_RELEASE_SHA}"
        )

    local_assets = verify_local_assets(asset_dir)
    matches = list_tag_releases(api, token)
    if not matches:
        raise RuntimeError(f"no GitHub Release record exists for {TAG}")

    release, verification_failures = choose_canonical_release(api, token, matches)
    if release is None:
        drafts = [
            item
            for item in matches
            if item.get("draft") is True and isinstance(item.get("id"), int)
        ]
        if not drafts:
            diagnostics = "; ".join(
                f"{release_id}: {reason}" for release_id, reason in sorted(verification_failures.items())
            )
            raise RuntimeError(
                "no complete verified same-tag release and no draft is available for safe repair; "
                + diagnostics
            )
        drafts.sort(key=lambda item: int(item["id"]))
        release = rebuild_draft_from_local(api, token, drafts[0], local_assets)

    release_id = release.get("id")
    if not isinstance(release_id, int):
        raise RuntimeError("canonical release has no numeric id")
    verify_remote_release_assets(api, token, release)

    release = json_request(
        "PATCH",
        f"{api}/releases/{release_id}",
        token,
        payload={"name": RELEASE_NAME, "draft": False, "prerelease": False},
    )
    if not isinstance(release, dict):
        raise RuntimeError("release metadata update returned an invalid response")
    if (
        release.get("name") != RELEASE_NAME
        or release.get("tag_name") != TAG
        or release.get("draft") is not False
        or release.get("prerelease") is not False
    ):
        raise RuntimeError("release metadata did not reach the required final state")

    assets, payload_names = verify_remote_release_assets(api, token, release)

    duplicates = [item for item in matches if item.get("id") != release_id]
    deleted_release_ids = [delete_duplicate_release(api, token, item) for item in duplicates]
    final_matches = list_tag_releases(api, token)
    if len(final_matches) != 1 or final_matches[0].get("id") != release_id:
        raise RuntimeError("duplicate GitHub Release cleanup did not leave one canonical release")

    encoded_tag = urllib.parse.quote(TAG, safe="")
    tag_release = json_request("GET", f"{api}/releases/tags/{encoded_tag}", token)
    if not isinstance(tag_release, dict) or tag_release.get("id") != release_id:
        raise RuntimeError("GitHub tag endpoint does not resolve to the canonical release")
    if tag_release.get("name") != RELEASE_NAME:
        raise RuntimeError("canonical release title is not the organization naming pattern")
    verify_remote_release_assets(api, token, tag_release)

    cleanup = {
        "release/v0.0.1": delete_branch(api, token, "release/v0.0.1"),
        "fix/release-title-pattern": delete_branch(api, token, "fix/release-title-pattern"),
    }
    duplicate_summary = (
        ", ".join(f"`{item}`" for item in deleted_release_ids) if deleted_release_ids else "none"
    )
    comment = "\n".join(
        [
            "## v0.0.1 release closure",
            "",
            f"- Immutable tag: `{TAG}` → `{EXPECTED_RELEASE_SHA}` (unchanged)",
            f"- GitHub Release: **{RELEASE_NAME}**",
            f"- Canonical Release ID: `{release_id}`; published, non-draft, non-prerelease",
            f"- Duplicate Release records removed: {duplicate_summary}",
            f"- Published assets: **{len(assets)} total** (`SHA256SUMS` + {len(payload_names)} payload assets)",
            "- Every payload was downloaded and matched `SHA256SUMS`",
            "- Every published asset matched GitHub SHA-256 digest metadata",
            f"- Permanent release-title publisher fix on `main`: `{PERMANENT_FIX_SHA}`",
            "- Branch cleanup: "
            + ", ".join(f"`{branch}`: {state}" for branch, state in cleanup.items()),
            "",
            "The one-shot repair branch deletes itself after posting this proof.",
        ]
    )
    json_request("POST", f"{api}/issues/{RELEASE_PR}/comments", token, payload={"body": comment})
    delete_branch(api, token, REPAIR_BRANCH)
    print(comment)
    print(f"deleted one-shot repair branch {REPAIR_BRANCH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
