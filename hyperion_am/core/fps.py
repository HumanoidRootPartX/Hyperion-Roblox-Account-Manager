"""FPS unlocker — patch Roblox's ClientAppSettings.json.

Roblox caps the client at 60 FPS by default. Writing
``{"DFIntTaskSchedulerTargetFps": <n>}`` into each version's
``ClientSettings\\ClientAppSettings.json`` raises (or removes) that cap. We write
it into every installed version so it survives Roblox updates between launches.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

FPS_FLAG = "DFIntTaskSchedulerTargetFps"


def _client_settings_dirs() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    versions = Path(local) / "Roblox" / "Versions"
    dirs: list[Path] = []
    if versions.is_dir():
        for entry in versions.iterdir():
            if (entry / "RobloxPlayerBeta.exe").is_file():
                dirs.append(entry / "ClientSettings")
    return dirs


def apply_fps(fps: int) -> int:
    """Write the FPS cap into every Roblox version. Returns versions patched."""
    fps = max(1, int(fps))
    patched = 0
    for cs in _client_settings_dirs():
        try:
            cs.mkdir(parents=True, exist_ok=True)
            (cs / "ClientAppSettings.json").write_text(
                json.dumps({FPS_FLAG: fps}), "utf-8"
            )
            patched += 1
        except OSError:
            continue
    return patched


def clear_fps() -> int:
    """Remove the FPS override from every Roblox version. Returns files removed."""
    removed = 0
    for cs in _client_settings_dirs():
        f = cs / "ClientAppSettings.json"
        if f.exists():
            try:
                f.unlink()
                removed += 1
            except OSError:
                continue
    return removed
