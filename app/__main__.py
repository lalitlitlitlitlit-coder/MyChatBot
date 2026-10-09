"""Run the app: python -m app"""
import os
import sys

import uvicorn

from .config import ROOT, ConfigError, load_env_file, load_settings
from .main import create_app


def main():
    load_env_file(ROOT / ".env")
    try:
        settings = load_settings()
    except ConfigError as exc:
        sys.exit(f"Cannot start: {exc} See README.md for setup.")
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(create_app(settings), host=host, port=port)


if __name__ == "__main__":
    main()
