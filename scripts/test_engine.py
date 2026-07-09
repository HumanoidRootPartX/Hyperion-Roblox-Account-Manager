"""Engine smoke test: launch URI, link parsing, config, and the CSRF/auth flow.

    .venv\\Scripts\\python.exe scripts\\test_engine.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from hyperion_am.core import fps, launcher, singleton
from hyperion_am.core.roblox import RobloxClient

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
failures = 0


def check(name: str, cond: bool) -> None:
    global failures
    print(f"  [{PASS if cond else FAIL}] {name}")
    if not cond:
        failures += 1


def _decode(uri: str) -> str:
    """Return the decoded placelauncherurl segment of a launch URI."""
    seg = uri.split("+placelauncherurl:", 1)[1].split("+", 1)[0]
    return urllib.parse.unquote(seg)


def test_launch_uri() -> None:
    print("Launch URI:")
    btid = "123456789012"

    plain = launcher.build_launch_uri("TICKET", 5315046213, browser_tracker_id=btid)
    check("starts with roblox-player scheme", plain.startswith("roblox-player:1+launchmode:play"))
    check("carries the ticket", "gameinfo:TICKET" in plain)
    inner = _decode(plain)
    check("RequestGame for a plain place", "request=RequestGame&" in inner)
    check("has placeId", "placeId=5315046213" in inner)
    check("no gameId when no job", "gameId=" not in inner)

    job = launcher.build_launch_uri("T", 99, browser_tracker_id=btid, job_id="abc-def")
    inner = _decode(job)
    check("RequestGameJob when job given", "request=RequestGameJob" in inner)
    check("gameId set to job", "gameId=abc-def" in inner)

    follow = launcher.build_launch_uri("T", 0, browser_tracker_id=btid, follow_user_id=42)
    check("RequestFollowUser for follow", "request=RequestFollowUser" in _decode(follow))
    check("follow carries userId", "userId=42" in _decode(follow))

    vip = launcher.build_launch_uri("T", 7, browser_tracker_id=btid, link_code="LC123")
    inner = _decode(vip)
    check("RequestPrivateGame for VIP", "request=RequestPrivateGame" in inner)
    check("VIP carries linkCode", "linkCode=LC123" in inner)


def test_home_launch() -> None:
    print("Launch to home (app mode):")
    uri = launcher.build_app_launch_uri("TICKET", browser_tracker_id="123456789012")
    check("uses launchmode:app (home, not a game)", "launchmode:app" in uri)
    check("carries the ticket", "gameinfo:TICKET" in uri)
    check("does NOT contain a placelauncher/game", "placelauncherurl" not in uri and "placeId" not in uri)
    args = launcher.launch_via_exe_home  # function exists for custom-folder home launch
    check("custom-folder home launcher exists", callable(args))


def test_link_parsing() -> None:
    print("Private link parsing:")
    pid, lc = launcher.parse_private_link(
        "https://www.roblox.com/games/920587237/Adopt-Me?privateServerLinkCode=98765"
    )
    check("place id parsed", pid == 920587237)
    check("link code parsed", lc == "98765")
    pid2, lc2 = launcher.parse_private_link("not a link")
    check("non-link returns none", pid2 is None and lc2 is None)

    check("browser tracker id is numeric-ish", launcher.new_browser_tracker_id().isdigit())


def test_csrf_and_ticket() -> None:
    print("CSRF + auth ticket flow (mocked):")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/v1/users/authenticated"):
            return httpx.Response(200, json={"id": 777, "name": "tester", "displayName": "Tester"})
        if url.endswith("/v1/authentication-ticket"):
            if "x-csrf-token" not in request.headers:
                # First hit: reject and hand back a token (forces a retry).
                return httpx.Response(403, headers={"x-csrf-token": "TOKEN123"})
            return httpx.Response(200, headers={"rbx-authentication-ticket": "TICKET-OK"})
        return httpx.Response(404)

    async def run() -> None:
        client = RobloxClient()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            info = await client.validate_cookie("cookie")
            check("validate_cookie parses user", info and info["user_id"] == 777)
            ticket = await client.get_auth_ticket("cookie")
            check("auth ticket obtained after CSRF retry", ticket == "TICKET-OK")
            check("csrf token cached on client", client._csrf == "TOKEN123")
        finally:
            await client._client.aclose()

    asyncio.run(run())


def test_fps_unlocker() -> None:
    print("FPS unlocker (temp Roblox dir):")
    with tempfile.TemporaryDirectory() as tmp:
        # Build a fake Roblox install so we never touch the real one.
        ver = Path(tmp) / "Roblox" / "Versions" / "version-test"
        ver.mkdir(parents=True)
        (ver / "RobloxPlayerBeta.exe").write_bytes(b"")
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = tmp
        try:
            n = fps.apply_fps(240)
            check("apply patched one version", n == 1)
            f = ver / "ClientSettings" / "ClientAppSettings.json"
            check("ClientAppSettings.json written", f.exists())
            check("FPS flag set to 240", json.loads(f.read_text())["DFIntTaskSchedulerTargetFps"] == 240)
            removed = fps.clear_fps()
            check("clear removed the file", removed == 1 and not f.exists())
        finally:
            if old is not None:
                os.environ["LOCALAPPDATA"] = old


def test_custom_folder_launch() -> None:
    print("Custom Roblox folder launch:")
    with tempfile.TemporaryDirectory() as tmp:
        # Folder that directly contains the exe
        direct = Path(tmp) / "WEAO-build"
        direct.mkdir()
        (direct / "RobloxPlayerBeta.exe").write_bytes(b"")
        check("finds exe in given folder", launcher.player_exe_in_folder(str(direct)) is not None)
        # Parent one level up
        check("finds exe one level down", launcher.player_exe_in_folder(tmp) is not None)
        # Direct path to the exe
        check("accepts direct exe path", launcher.player_exe_in_folder(str(direct / "RobloxPlayerBeta.exe")) is not None)
        # Missing / blank
        check("missing folder -> None", launcher.player_exe_in_folder(str(Path(tmp) / "nope")) is None)
        check("blank -> None", launcher.player_exe_in_folder("") is None)

        exe = direct / "RobloxPlayerBeta.exe"
        args = launcher.build_exe_args(exe, "TICKET", 5315046213, browser_tracker_id="123456789012")
        check("direct args use --app", "--app" in args and args[0] == str(exe))
        check("direct args pass ticket after -t", args[args.index("-t") + 1] == "TICKET")
        join = args[args.index("-j") + 1]
        check("direct join url has placeId", "placeId=5315046213" in join)


def test_singleton() -> None:
    print("Multi-instance singleton:")
    if sys.platform != "win32":
        print("  [skip] not Windows")
        return
    try:
        ok = singleton.acquire()
        names = singleton.held_names()
        # NOTE: a currently-running Roblox may already own one singleton name (as a
        # kernel Event), so we can only co-create the free one(s). In the real
        # workflow you close all Roblox first, then both names are held.
        check("acquire() succeeds", ok)
        check("is_held() true, holds >=1 singleton lock", singleton.is_held() and len(names) >= 1)
        check("targets both known singleton names", set(singleton.SINGLETON_NAMES) ==
              {"ROBLOX_singletonEvent", "ROBLOX_singletonMutex"})
    finally:
        singleton.release()
        check("release() clears all handles", not singleton.is_held())


if __name__ == "__main__":
    test_launch_uri()
    test_home_launch()
    test_link_parsing()
    test_fps_unlocker()
    test_custom_folder_launch()
    test_singleton()
    test_csrf_and_ticket()
    print()
    if failures:
        print(f"\033[91m{failures} check(s) failed.\033[0m")
        sys.exit(1)
    print("\033[92mAll checks passed.\033[0m")
