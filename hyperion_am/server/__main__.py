"""``python -m hyperion_am.server`` — run the API/UI server without a window."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("hyperion_am.server.app:app", host="127.0.0.1", port=8777, log_level="info")
