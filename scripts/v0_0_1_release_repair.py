from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_VERSION = "2022-11-28"
TAG = "v0.0.1"
RELEASE_NAME = "ProdKit Control v0.0.1"
EXPECTED_RELEASE_SHA = "889a754f416bd9454bc9e7d407a6f3d0327241d8"
PERMANENT_FIX_SHA = "d3649917ad7e0e115e5860d1ef2da8769e1e49f5"
RELEASE_PR = 6
REPAIR_BRANCH = "release-repair/v0.0.1"


def request(
    method: str,
    url: str,
    token: str,
    *,
    payload: dict[str, Any] | None = None,
    accept: str = "application/vnd.github+json",
    allow_404: bool = False,
) -> tuple[int, bytes] | None:
    headers = {
        "Accept": accept,
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "prodkit-control-v0.0.1-release-repair",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
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


def delete_branch(api: str, token: str, branch: str) -> str:
    encoded = urllib.parse.quote(branch, safe="")
    result = request("DELETE", f"{api}/git/refs/heads/{encoded}", token, allow_404=True)
    return "already absent" if result is None else "deleted"


def main() -> int:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repo or "/" not in repo:
        raise RuntimeError("GITHUB_REPOSITORY must be owner/name")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    api = f"https://api.github.com/repos/{repo}"

    target_sha = resolve_tag_commit(api, token)
    if target_sha != EXPECTED_RELEASE_SHA:
        raise RuntimeError(
            f"immutable tag {TAG} points at {target_sha}, expected {EXPECTED_RELEASE_SHA}"
        )

    encoded_tag = urllib.parse.quote(TAG, safe="")
    release = json_request("GET", f"{api}/releases/tags/{encoded_tag}", token)
    if not isinstance(release, dict):
        raise RuntimeError(f"GitHub Release for {TAG} does not exist")
    release_id = release.get("id")
    if not isinstance(release_id, int):
        raise RuntimeError("release has no numeric id")
    if release.get("tag_name") != TAG:
        raise RuntimeError("release tag_name does not match immutable tag")

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

    assets = json_request("GET", f"{api}/releases/{release_id}/assets?per_page=100", token)
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("published release has no assets")
    by_name: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            raise RuntimeError("release contains malformed asset metadata")
        name = asset["name"]
        if name in by_name:
            raise RuntimeError(f"duplicate published release asset: {name}")
        by_name[name] = asset
    if "SHA256SUMS" not in by_name:
        raise RuntimeError("published release is missing SHA256SUMS")

    sums_bytes = download(by_name["SHA256SUMS"], token)
    verify_github_digest(by_name["SHA256SUMS"], sums_bytes)
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

    payload_names = set(by_name) - {"SHA256SUMS"}
    if set(checksums) != payload_names:
        raise RuntimeError(
            f"published asset set does not match SHA256SUMS: assets={sorted(payload_names)} "
            f"checksums={sorted(checksums)}"
        )
    for name in sorted(payload_names):
        data = download(by_name[name], token)
        actual = verify_github_digest(by_name[name], data)
        if actual != checksums[name]:
            raise RuntimeError(f"SHA256SUMS mismatch for {name}: {actual} != {checksums[name]}")

    cleanup = {
        "release/v0.0.1": delete_branch(api, token, "release/v0.0.1"),
        "fix/release-title-pattern": delete_branch(api, token, "fix/release-title-pattern"),
    }
    comment = "\n".join(
        [
            "## v0.0.1 release closure",
            "",
            f"- Immutable tag: `{TAG}` → `{EXPECTED_RELEASE_SHA}` (unchanged)",
            f"- GitHub Release: **{RELEASE_NAME}**",
            f"- Release ID: `{release_id}`; published, non-draft, non-prerelease",
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
