#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".prodkit/workflows/release-build.sh"


def main() -> None:
    text = BUILD.read_text()
    required = (
        "-name '*.whl'",
        "-name '*.tar.gz'",
        "-name '*.tgz'",
        "central release contract rejects hidden names",
    )
    for fragment in required:
        if fragment not in text:
            raise SystemExit(f"release output contract missing: {fragment}")

    broad_copy = "find .artifacts/release-build -maxdepth 1 -type f -exec cp"
    if broad_copy in text:
        raise SystemExit("release output contract must not copy every build-directory file")

    print("release output contract passed")


if __name__ == "__main__":
    main()
