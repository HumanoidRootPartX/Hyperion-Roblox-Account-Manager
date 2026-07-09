"""Username + password login via a real browser (Playwright).

Roblox's login endpoint is gated by Arkose FunCaptcha and email/2FA, so there is
no free, reliable, headless username:password API. Instead we drive a *visible*
Chromium window: prefill the credentials, let the human clear any captcha / 2FA,
and capture the ``.ROBLOSECURITY`` session cookie the moment login succeeds.

Playwright is an optional dependency. If it (or its browser) isn't installed we
raise :class:`LoginUnavailable` with the exact install command.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Optional

LOGIN_URL = "https://www.roblox.com/login"
COOKIE_NAME = ".ROBLOSECURITY"

StatusCb = Optional[Callable[[str], Awaitable[None]]]


class LoginError(Exception):
    pass


class LoginUnavailable(LoginError):
    pass


async def _emit(cb: StatusCb, message: str) -> None:
    if cb is not None:
        await cb(message)


async def login_with_credentials(
    username: str,
    password: str,
    *,
    on_status: StatusCb = None,
    timeout_secs: int = 240,
) -> str:
    """Open a browser, prefill credentials, and return the captured cookie.

    Raises :class:`LoginUnavailable` if Playwright isn't installed, or
    :class:`LoginError` on timeout / failure.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise LoginUnavailable(
            "Username/password login needs Playwright. Install it with:\n"
            "    pip install playwright\n    playwright install chromium"
        ) from exc

    await _emit(on_status, "Opening Roblox login…")

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=False)
        except Exception as exc:
            raise LoginUnavailable(
                "Chromium isn't installed for Playwright. Run:\n"
                "    playwright install chromium"
            ) from exc

        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")

            # Best-effort prefill. Selectors are stable-ish; if Roblox changes
            # them the user can still type into the open window themselves.
            for sel in ("#login-username", 'input[name="username"]'):
                try:
                    await page.fill(sel, username, timeout=4000)
                    break
                except Exception:
                    continue
            for sel in ("#login-password", 'input[name="password"]'):
                try:
                    await page.fill(sel, password, timeout=4000)
                    break
                except Exception:
                    continue
            for sel in ("#login-button", 'button[type="submit"]'):
                try:
                    await page.click(sel, timeout=4000)
                    break
                except Exception:
                    continue

            await _emit(
                on_status,
                "Complete any captcha / 2FA in the browser window — "
                "Hyperion will grab the session automatically.",
            )

            # Poll for the auth cookie until login completes (or we time out).
            deadline = asyncio.get_event_loop().time() + timeout_secs
            while asyncio.get_event_loop().time() < deadline:
                for c in await context.cookies():
                    if c.get("name") == COOKIE_NAME and c.get("value"):
                        await _emit(on_status, "Login captured ✓")
                        return c["value"]
                await asyncio.sleep(1.0)

            raise LoginError("Timed out waiting for login to complete.")
        finally:
            await context.close()
            await browser.close()
