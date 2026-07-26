from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from .config import discover_project_root


def main() -> None:
    project_root = discover_project_root()
    os.environ.setdefault("POLITICA_PROJECT_ROOT", str(project_root))
    load_dotenv(project_root / ".env", override=False)
    parser = argparse.ArgumentParser(description="Run the local Politica ingestion application.")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("POLITICA_APP_PORT", "8765")),
        help="Local TCP port (default: 8765 or POLITICA_APP_PORT).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the operator interface in the default browser.",
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        "politica_erd.app.api:app",
        host="127.0.0.1",
        port=args.port,
        reload=False,
        access_log=True,
    )


if __name__ == "__main__":
    main()
