"""Entry point: `uv run python -m kai.main` or `uv run uvicorn kai.main:app`."""

from __future__ import annotations

import os

from .app import create_app

app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("kai.main:app", host=os.environ.get("KAI_HOST", "127.0.0.1"),
                port=int(os.environ.get("KAI_PORT", "8000")), reload=False)


if __name__ == "__main__":
    main()
