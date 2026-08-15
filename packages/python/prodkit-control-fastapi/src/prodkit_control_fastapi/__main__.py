from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "prodkit_control_fastapi.app:create_app",
        factory=True,
        host=os.getenv("PRODKIT_API_HOST", "127.0.0.1"),
        port=int(os.getenv("PRODKIT_API_PORT", "8000")),
        log_level=os.getenv("PRODKIT_LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
