"""Hyperion Account Manager — desktop entry point.

Starts the local FastAPI server on a background thread, waits for it to accept
connections, then opens a native pywebview window pointed at the web UI.

Run with ``--no-window`` to skip the desktop shell and just serve the UI at
http://127.0.0.1:8777/app/ (useful for development in a normal browser).
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time

import uvicorn

from hyperion_am import APP_NAME, __version__

HOST = "127.0.0.1"
PORT = 8777
URL = f"http://{HOST}:{PORT}/app/"


def _serve() -> None:
    from hyperion_am.server.app import app

    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def _wait_for_server(timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="serve the UI without opening the desktop window",
    )
    args = parser.parse_args()

    if args.no_window:
        print(f"{APP_NAME} v{__version__} — serving at {URL} (Ctrl+C to stop)")
        _serve()
        return

    threading.Thread(target=_serve, daemon=True).start()
    if not _wait_for_server():
        print("ERROR: the local server failed to start.", file=sys.stderr)
        sys.exit(1)

    import webview

    webview.create_window(
        f"{APP_NAME} v{__version__}",
        URL,
        width=1280,
        height=820,
        min_size=(1000, 640),
        maximized=True,
    )
    webview.start()


if __name__ == "__main__":
    main()
