from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_VERSION = "2022-11-28"


def _request(
    method: str,
    url: str,
    token: str,
    *,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    body = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "prodkit-control-release-tag",
    }
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        # The origin is fixed to GitHub's API and path components are URL-encoded.
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return 404, None
        payload = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed ({exc.code}): {payload}") from exc


def _ref(repository: str, ref: str, token: str) -> dict[str, Any] | None:
    encoded_ref = urllib.parse.quote(ref, safe="/")
    status, payload = _request(
        "GET",
        f"https://api.github.com/repos/{repository}/git/ref/{encoded_ref}",
        token,
    )
    if status == 404:
        return None
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid ref response")
    return payload


def _assert_exact_commit(ref: dict[str, Any], *, expected_ref: str, sha: str) -> None:
    if ref.get("ref") != f"refs/{expected_ref}":
        raise RuntimeError(f"GitHub returned an unexpected ref for {expected_ref}")
    target = ref.get("object")
    if not isinstance(target, dict):
        raise RuntimeError(f"ref {expected_ref} has no target object")
    if target.get("type") != "commit" or target.get("sha") != sha:
        raise RuntimeError(f"ref {expected_ref} does not resolve directly to expected commit {sha}")


def ensure(tag: str, sha: str) -> None:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repository or "/" not in repository:
        raise RuntimeError("GITHUB_REPOSITORY must be owner/name")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    if not tag.startswith("v") or not tag[1:]:
        raise ValueError("release tag must be v-prefixed")
    if len(sha) not in {40, 64} or any(character not in "0123456789abcdef" for character in sha):
        raise ValueError("release SHA must be a lowercase Git object id")

    main_ref = _ref(repository, "heads/main", token)
    if main_ref is None:
        raise RuntimeError("main branch ref is missing")
    _assert_exact_commit(main_ref, expected_ref="heads/main", sha=sha)

    tag_ref_name = f"tags/{tag}"
    existing = _ref(repository, tag_ref_name, token)
    if existing is None:
        _, payload = _request(
            "POST",
            f"https://api.github.com/repos/{repository}/git/refs",
            token,
            json_body={"ref": f"refs/tags/{tag}", "sha": sha},
        )
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub returned an invalid tag creation response")
        existing = payload

    _assert_exact_commit(existing, expected_ref=tag_ref_name, sha=sha)
    verified = _ref(repository, tag_ref_name, token)
    if verified is None:
        raise RuntimeError(f"tag {tag} disappeared after creation")
    _assert_exact_commit(verified, expected_ref=tag_ref_name, sha=sha)
    print(f"Verified immutable lightweight tag {tag} -> {sha}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or verify an exact GitHub release tag")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    ensure(args.tag, args.sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
