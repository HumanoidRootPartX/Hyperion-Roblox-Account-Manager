"""Async Roblox web-API client.

Handles the two quirks of Roblox's API: CSRF tokens (a request returns ``403``
with a fresh token in ``x-csrf-token`` which we capture and retry) and 429 rate
limiting. Cookies are passed per-call so one client serves all accounts.
"""

from __future__ import annotations

from typing import Iterable, Optional

import httpx

USER_AGENT = "HyperionAM/0.1 (+local)"
GAME_REFERER = "https://www.roblox.com/games/4924922222/Brookhaven-RP"


class RobloxError(Exception):
    pass


class AuthError(RobloxError):
    pass


class RateLimited(RobloxError):
    pass


class RobloxClient:
    def __init__(self) -> None:
        self._csrf: Optional[str] = None
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        cookie: str = "",
        json_body: Optional[dict] = None,
        headers: Optional[dict] = None,
        _retry: int = 0,
    ) -> httpx.Response:
        h: dict[str, str] = dict(headers or {})
        if cookie:
            h["Cookie"] = f".ROBLOSECURITY={cookie}"
        if self._csrf:
            h["X-CSRF-TOKEN"] = self._csrf
        h.setdefault("Referer", "https://www.roblox.com/")
        if method.upper() == "POST" and json_body is None:
            h.setdefault("Content-Type", "application/json")

        resp = await self._client.request(method, url, headers=h, json=json_body)

        if resp.status_code == 403 and "x-csrf-token" in resp.headers and _retry == 0:
            self._csrf = resp.headers["x-csrf-token"]
            return await self._request(
                method, url, cookie=cookie, json_body=json_body,
                headers=headers, _retry=1,
            )
        if resp.status_code == 429:
            raise RateLimited("Roblox is rate-limiting requests (429). Try again shortly.")
        return resp

    # ----------------------------------------------------------- validation
    async def validate_cookie(self, cookie: str) -> Optional[dict]:
        """Return ``{user_id, username, display_name}`` or ``None`` if invalid."""
        r = await self._request(
            "GET", "https://users.roblox.com/v1/users/authenticated", cookie=cookie
        )
        if r.status_code == 200:
            d = r.json()
            return {
                "user_id": int(d["id"]),
                "username": d.get("name", ""),
                "display_name": d.get("displayName", ""),
            }
        return None

    # --------------------------------------------------------- auth ticket
    async def get_auth_ticket(self, cookie: str) -> str:
        """Generate a short-lived authentication ticket for launching."""
        r = await self._request(
            "POST",
            "https://auth.roblox.com/v1/authentication-ticket",
            cookie=cookie,
            headers={"Referer": GAME_REFERER},
        )
        if r.status_code in (200, 201):
            ticket = r.headers.get("rbx-authentication-ticket")
            if ticket:
                return ticket
        raise AuthError(
            f"Couldn't get an auth ticket (HTTP {r.status_code}). "
            "The account's cookie is probably expired — re-add it."
        )

    # ------------------------------------------------------------ presence
    async def get_presences(self, cookie: str, user_ids: Iterable[int]) -> dict[int, dict]:
        ids = [int(i) for i in user_ids]
        if not ids:
            return {}
        r = await self._request(
            "POST",
            "https://presence.roblox.com/v1/presence/users",
            cookie=cookie,
            json_body={"userIds": ids},
        )
        out: dict[int, dict] = {}
        if r.status_code == 200:
            for p in r.json().get("userPresences", []):
                out[int(p["userId"])] = {
                    "type": p.get("userPresenceType", 0),
                    "place_id": p.get("placeId"),
                    "game_id": p.get("gameId"),
                    "last_location": p.get("lastLocation", ""),
                }
        return out

    # ------------------------------------------------------------- avatars
    async def get_avatars(self, user_ids: Iterable[int]) -> dict[int, str]:
        ids = [int(i) for i in user_ids]
        if not ids:
            return {}
        joined = ",".join(str(i) for i in ids)
        url = (
            "https://thumbnails.roblox.com/v1/users/avatar-headshot"
            f"?userIds={joined}&size=150x150&format=Png&isCircular=false"
        )
        r = await self._request("GET", url)
        out: dict[int, str] = {}
        if r.status_code == 200:
            for e in r.json().get("data", []):
                if e.get("imageUrl"):
                    out[int(e["targetId"])] = e["imageUrl"]
        return out

    # ----------------------------------------------------- username lookup
    async def lookup_username(self, username: str) -> Optional[dict]:
        r = await self._request(
            "POST",
            "https://users.roblox.com/v1/usernames/users",
            json_body={"usernames": [username], "excludeBannedUsers": False},
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            if data:
                e = data[0]
                return {
                    "user_id": int(e["id"]),
                    "username": e.get("name", ""),
                    "display_name": e.get("displayName", ""),
                }
        return None
