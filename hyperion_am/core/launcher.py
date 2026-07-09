"""Build the ``roblox-player:`` launch URI and hand it to the Windows shell.

The join flow once we have an auth ticket:

    ShellExecute("open", roblox-player:1+launchmode:play+gameinfo:<ticket>
                 +launchtime:<ms>+placelauncherurl:<urlencoded PlaceLauncher.ashx>
                 +browsertrackerid:<btid>+robloxLocale:en_us+gameLocale:en_us
                 +channel:+LaunchExp:InApp)

The inner PlaceLauncher URL selects what to join: a place (optionally a specific
job/server), following a user, or a private/VIP server.
"""

from __future__ import annotations

import os
import random
import re
import sys
import urllib.parse
from pathlib import Path

ASSETGAME = "https://assetgame.roblox.com/game/PlaceLauncher.ashx"


def new_browser_tracker_id() -> str:
    """Mimic RAM's BrowserTrackerID shape (used to identify our own instances)."""
    return f"{random.randint(100000, 175000)}{random.randint(100000, 900000)}"


def build_placelauncher_url(
    place_id: int,
    *,
    browser_tracker_id: str,
    job_id: str | None = None,
    access_code: str | None = None,
    link_code: str | None = None,
    follow_user_id: int | None = None,
    teleport: bool = False,
) -> str:
    if follow_user_id:
        return (
            f"{ASSETGAME}?request=RequestFollowUser"
            f"&browserTrackerId={browser_tracker_id}&userId={follow_user_id}"
        )
    if access_code or link_code:
        return (
            f"{ASSETGAME}?request=RequestPrivateGame"
            f"&browserTrackerId={browser_tracker_id}&placeId={place_id}"
            f"&accessCode={access_code or ''}&linkCode={link_code or ''}"
        )
    request = "RequestGameJob" if job_id else "RequestGame"
    url = (
        f"{ASSETGAME}?request={request}"
        f"&browserTrackerId={browser_tracker_id}&placeId={place_id}"
        f"&isPlayTogetherGame=false"
    )
    if job_id:
        url += f"&gameId={job_id}"
    if teleport:
        url += "&isTeleport=true"
    return url


def build_launch_uri(ticket: str, place_id: int, *, browser_tracker_id: str, **kw) -> str:
    import time

    launchtime = int(time.time() * 1000)
    placelauncher = build_placelauncher_url(
        place_id, browser_tracker_id=browser_tracker_id, **kw
    )
    encoded = urllib.parse.quote(placelauncher, safe="")
    return (
        f"roblox-player:1+launchmode:play+gameinfo:{ticket}"
        f"+launchtime:{launchtime}+placelauncherurl:{encoded}"
        f"+browsertrackerid:{browser_tracker_id}"
        f"+robloxLocale:en_us+gameLocale:en_us+channel:+LaunchExp:InApp"
    )


def player_exe_in_folder(folder: str) -> Path | None:
    """Find RobloxPlayerBeta.exe given a custom folder.

    Accepts the folder that contains the exe directly, a parent folder one level
    up, or a direct path to the exe itself. Returns ``None`` if not found.
    """
    if not folder:
        return None
    p = Path(folder)
    if p.is_file() and p.name.lower() == "robloxplayerbeta.exe":
        return p
    if not p.exists():
        return None
    direct = p / "RobloxPlayerBeta.exe"
    if direct.is_file():
        return direct
    if p.is_dir():
        for child in p.iterdir():
            candidate = child / "RobloxPlayerBeta.exe"
            if candidate.is_file():
                return candidate
    return None


def build_exe_args(exe_path, ticket: str, place_id: int, *, browser_tracker_id: str, **kw) -> list[str]:
    """Direct-launch arguments for an explicit RobloxPlayerBeta.exe.

    Uses the long-standing ``--app -t <ticket> -j <placelauncherurl>`` form that
    works across most modern and many older/archived clients.
    """
    url = build_placelauncher_url(place_id, browser_tracker_id=browser_tracker_id, **kw)
    return [str(exe_path), "--app", "-t", str(ticket), "-j", url]


def launch_via_exe(exe_path, ticket: str, place_id: int, *, browser_tracker_id: str, **kw) -> list[str]:
    """Launch a specific RobloxPlayerBeta.exe directly (custom/older version)."""
    import subprocess

    args = build_exe_args(exe_path, ticket, place_id, browser_tracker_id=browser_tracker_id, **kw)
    subprocess.Popen(args, cwd=str(Path(exe_path).parent))
    return args


def build_app_launch_uri(ticket: str, *, browser_tracker_id: str) -> str:
    """Open Roblox to the HOME screen (logged in), without joining a game.

    ``launchmode:app`` launches the desktop app instead of a game; ``gameinfo``
    still carries the auth ticket so the client is signed in as the account.
    """
    import time

    launchtime = int(time.time() * 1000)
    return (
        f"roblox-player:1+launchmode:app+gameinfo:{ticket}"
        f"+launchtime:{launchtime}+browsertrackerid:{browser_tracker_id}"
        f"+robloxLocale:en_us+gameLocale:en_us+channel:+LaunchExp:InApp"
    )


def launch_via_exe_home(exe_path, ticket: str) -> list[str]:
    """Open the HOME screen for a custom RobloxPlayerBeta.exe (no ``-j`` join)."""
    import subprocess

    args = [str(exe_path), "--app", "-t", str(ticket)]
    subprocess.Popen(args, cwd=str(Path(exe_path).parent))
    return args


def shell_launch(uri: str) -> None:
    """Invoke the registered roblox-player protocol handler."""
    if sys.platform != "win32":
        raise RuntimeError("Launching Roblox is only supported on Windows.")
    import ctypes

    rc = ctypes.windll.shell32.ShellExecuteW(None, "open", uri, None, None, 1)
    if int(rc) <= 32:
        raise RuntimeError(
            f"Windows could not open the Roblox launcher (code {rc}). "
            "Make sure Roblox is installed."
        )


def parse_private_link(text: str) -> tuple[int | None, str | None]:
    """Extract ``(place_id, link_code)`` from a classic VIP/private server URL.

    Handles e.g. ``https://www.roblox.com/games/123?privateServerLinkCode=456``.
    Returns ``(None, None)`` if not a recognisable private link.
    """
    link_code = None
    m = re.search(r"privateServerLinkCode=([0-9A-Za-z_\-]+)", text)
    if m:
        link_code = m.group(1)
    place_id = None
    m = re.search(r"/games/(\d+)", text)
    if m:
        place_id = int(m.group(1))
    return place_id, link_code


def find_roblox_player() -> Path | None:
    """Locate RobloxPlayerBeta.exe under the standard install root."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    versions = Path(local) / "Roblox" / "Versions"
    if versions.is_dir():
        for entry in versions.iterdir():
            candidate = entry / "RobloxPlayerBeta.exe"
            if candidate.is_file():
                return candidate
    return None


def clear_roblox_cookies() -> bool:
    """Truncate RobloxCookies.dat (privacy mode). Returns True if cleared."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return False
    path = Path(local) / "Roblox" / "LocalStorage" / "RobloxCookies.dat"
    if not path.exists():
        return False
    try:
        path.write_bytes(b"")
        return True
    except OSError:
        return False
