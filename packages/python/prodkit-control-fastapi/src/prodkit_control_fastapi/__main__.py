from __future__ import annotations

import os

import uvicorn

from .app import create_app


def _env_bool(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def main() -> None:
    app = create_app(allow_insecure_header_auth=_env_bool("PRODKIT_ALLOW_INSECURE_HEADER_AUTH"))
    uvicorn.run(
        app,
        host=os.getenv("PRODKIT_API_HOST", "127.0.0.1"),
        port=int(os.getenv("PRODKIT_API_PORT", "8000")),
        log_level=os.getenv("PRODKIT_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
