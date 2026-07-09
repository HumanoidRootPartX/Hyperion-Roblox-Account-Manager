"""Roblox process management via psutil — enumerate, count, and kill clients."""

from __future__ import annotations

import psutil

PROC_NAME = "robloxplayerbeta.exe"


def roblox_processes() -> list[psutil.Process]:
    out: list[psutil.Process] = []
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() == PROC_NAME:
                out.append(p)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return out


def instance_count() -> int:
    return len(roblox_processes())


def kill_all() -> int:
    killed = 0
    for p in roblox_processes():
        try:
            p.kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed


def kill_by_tracker(browser_tracker_id: str) -> int:
    """Kill the Roblox client launched for a given BrowserTrackerID.

    Each launch tags its process command line with ``-b <id>``; matching on that
    lets us close an account's previous instance before relaunching it (avoids
    Roblox's "same account launched elsewhere" disconnect).
    """
    if not browser_tracker_id:
        return 0
    # The tracker id appears as "-b <id>" (protocol launch) or inside the
    # placelauncher URL as "browserTrackerId=<id>" (direct exe launch). The id is
    # an ~11-digit number, so matching the raw value covers both with no realistic
    # risk of a false positive.
    killed = 0
    for p in roblox_processes():
        try:
            cmdline = " ".join(p.cmdline() or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if browser_tracker_id in cmdline:
            try:
                p.kill()
                killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    return killed
