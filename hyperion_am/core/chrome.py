"""Open a dedicated, auto-logged-in Chromium window per account.

Each account gets an isolated persistent browser profile under
``%APPDATA%\\HyperionAccountManager\\ChromeProfiles\\<username>``. We seed the
account's ``.ROBLOSECURITY`` cookie into that profile (via the bundled Playwright
Chromium, headless), then launch a **detached** Chromium on the same profile so
the window stays open independently and is already signed in to Roblox.

Playwright is an optional dependency; if it (or its browser) isn't installed we
raise :class:`ChromeUnavailable` with the fix.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import subprocess
import sys
from pathlib import Path

from . import paths


class ChromeError(Exception):
    pass


class ChromeUnavailable(ChromeError):
    pass


def _safe_name(username: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", username or "account").strip("_")
    return slug or "account"


def profile_dir(username: str) -> Path:
    d = paths.data_dir() / "ChromeProfiles" / _safe_name(username)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clear_singleton_locks(profile: Path) -> None:
    """Remove stale single-instance lock files left by the seeding browser.

    If these linger, the visible relaunch thinks another Chrome already owns the
    profile, forwards its URL to that (now-dead) instance, and exits immediately —
    which looked like "opens and closes in an instant".
    """
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        with contextlib.suppress(OSError):
            p = profile / name
            if p.exists() or p.is_symlink():
                p.unlink()


async def open_in_chrome(username: str, cookie: str) -> Path:
    """Seed the account's cookie into its profile, then open a detached Chromium.

    Returns the profile directory. Raises :class:`ChromeUnavailable` if Playwright
    or its Chromium isn't installed.
    """
    if not cookie:
        raise ChromeError(f"{username} has no saved cookie — re-add it.")
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ChromeUnavailable(
            "Opening accounts in Chrome needs Playwright:\n"
            "    pip install playwright\n    playwright install chromium"
        ) from exc

    profile = profile_dir(username)

    async with async_playwright() as pw:
        try:
            ctx = await pw.chromium.launch_persistent_context(str(profile), headless=True)
        except Exception as exc:
            raise ChromeUnavailable(
                "Chromium isn't installed for Playwright. Run:\n"
                "    playwright install chromium"
            ) from exc
        try:
            await ctx.add_cookies([{
                "name": ".ROBLOSECURITY", "value": cookie,
                "domain": ".roblox.com", "path": "/",
                "httpOnly": True, "secure": True,
            }])
            page = await ctx.new_page()
            try:
                await page.goto("https://www.roblox.com/home", wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass  # cookie is already committed; navigation is best-effort
        finally:
            await ctx.close()
        exe = pw.chromium.executable_path

    # Let the headless process fully exit and release its profile locks before
    # we relaunch a visible Chromium on the same profile.
    await asyncio.sleep(0.8)
    _clear_singleton_locks(profile)

    args = [
        exe,
        f"--user-data-dir={profile}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session=false",
        "--disable-session-crashed-bubble",
        "--disable-features=InfiniteSessionRestore",
        "https://www.roblox.com/home",
    ]
    kwargs: dict = {"close_fds": True}
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP → survives the app closing.
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    try:
        subprocess.Popen(args, **kwargs)
    except OSError as exc:
        raise ChromeError(f"Couldn't launch Chromium: {exc}") from exc
    return profile
