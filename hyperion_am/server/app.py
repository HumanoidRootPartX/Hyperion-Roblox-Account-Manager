"""FastAPI app: REST control plane + WebSocket event bus + static web UI.

One process-local :class:`Vault` holds decrypted accounts in memory while
unlocked; one shared :class:`RobloxClient` serves all Roblox API calls. The UI is
a pure client of these endpoints and the ``/ws`` event stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import APP_NAME, __version__
from ..core import chrome, fps, launcher, migrate, optimizer, paths, process, singleton
from ..core.config import Config
from ..core.login import LoginError, LoginUnavailable, login_with_credentials
from ..core.models import Account
from ..core.roblox import AuthError, RateLimited, RobloxClient, RobloxError
from ..core.vault import BadPassword, Vault, VaultError, VaultLocked

app = FastAPI(title=APP_NAME, version=__version__)
vault = Vault()
roblox = RobloxClient()
config = Config.load()


_watcher_task: "asyncio.Task | None" = None
_optimizer_task: "asyncio.Task | None" = None
_miss_counts: dict[int, int] = {}
_optimizer = optimizer.Optimizer()


@app.on_event("startup")
async def _startup() -> None:
    global _watcher_task, _optimizer_task
    if config.multi_instance:
        singleton.acquire()
    _watcher_task = asyncio.create_task(_watcher_loop())
    _optimizer_task = asyncio.create_task(_optimizer_loop())


@app.on_event("shutdown")
async def _shutdown() -> None:
    for task in (_watcher_task, _optimizer_task):
        if task is not None:
            task.cancel()
    await roblox.aclose()


def _main_btid() -> str:
    """browser-tracker-id of the configured MAIN account (for the optimizer)."""
    uid = config.optimizer_main_user_id
    if uid and vault.unlocked:
        acc = vault.get(int(uid))
        if acc:
            return acc.browser_tracker_id or ""
    return ""


async def _optimizer_loop() -> None:
    """Tick the optimizer while it's enabled (minimize/trim alts, spare the main)."""
    while True:
        try:
            await asyncio.sleep(2)
            if not config.optimizer_enabled:
                continue
            cfg = optimizer.OptConfig(
                main_btid=_main_btid(),
                soft_ram_mb=int(config.optimizer_soft_ram_mb or 500),
                trim_interval_secs=int(config.optimizer_trim_interval_secs or 30),
                warmup_minutes=float(config.optimizer_warmup_minutes or 0.5),
                minimize_alts=bool(config.optimizer_minimize_alts),
                bot_cores=int(config.optimizer_bot_cores or 3),
                kill_crashhandler=bool(config.optimizer_kill_crashhandler),
            )
            await asyncio.to_thread(_optimizer.tick, cfg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let the loop die
            await bus.broadcast({"type": "optimizer_error", "message": str(exc)})


async def _watcher_loop() -> None:
    """Periodically relaunch 'keep online' accounts that fall out of game.

    Presence-based (no log tailing): every interval we poll presence for the
    watched accounts and relaunch any that haven't been in-game for
    ``watcher_grace_checks`` consecutive cycles, reusing their last launch target.
    Reads ``config`` each cycle so toggling the watcher needs no restart.
    """
    while True:
        try:
            interval = max(10, int(config.watcher_interval_secs or 30))
            await asyncio.sleep(interval)
            if not config.watcher_enabled or not vault.unlocked:
                continue
            watched = [a for a in vault.accounts() if a.keep_alive and a.last_launch]
            if not watched:
                continue
            cookie = _first_cookie()
            if not cookie:
                continue
            presences = await roblox.get_presences(cookie, [a.user_id for a in watched])
            grace = max(1, int(config.watcher_grace_checks or 2))
            for a in watched:
                p = presences.get(a.user_id)
                if p and p.get("type") == 2:  # In Game
                    _miss_counts[a.user_id] = 0
                    continue
                _miss_counts[a.user_id] = _miss_counts.get(a.user_id, 0) + 1
                if _miss_counts[a.user_id] >= grace:
                    _miss_counts[a.user_id] = 0
                    try:
                        await _relaunch(a)
                        await toast(f"Watcher relaunched {a.label} (was not in game).", "info")
                    except Exception as exc:  # noqa: BLE001 - keep the loop alive
                        await toast(f"Watcher: relaunch failed for {a.label}: {exc}", "error")
            await emit_accounts()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let the loop die
            await bus.broadcast({"type": "watcher_error", "message": str(exc)})


# --------------------------------------------------------------------- events
class EventBus:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, event: dict) -> None:
        async with self._lock:
            targets = list(self._clients)
        for ws in targets:
            try:
                await ws.send_json(event)
            except Exception:
                await self.disconnect(ws)


bus = EventBus()


def vault_state() -> dict:
    return {
        "exists": vault.exists(),
        "unlocked": vault.unlocked,
        "account_count": vault.account_count if vault.unlocked else None,
    }


def config_state() -> dict:
    return {
        **config.as_dict(),
        "multi_instance_held": singleton.is_held(),
        "roblox_running": process.instance_count(),
    }


async def emit_state() -> None:
    await bus.broadcast({"type": "state", **vault_state()})


async def emit_accounts() -> None:
    accounts = [a.public_dict() for a in vault.accounts()] if vault.unlocked else []
    await bus.broadcast({"type": "accounts", "accounts": accounts})


async def emit_config() -> None:
    await bus.broadcast({"type": "config", "config": config_state()})


async def toast(message: str, level: str = "info") -> None:
    await bus.broadcast({"type": "toast", "level": level, "message": message})


def _first_cookie() -> str:
    for a in vault.accounts():
        if a.cookie:
            return a.cookie
    return ""


# --------------------------------------------------------------- request bodies
class PasswordBody(BaseModel):
    password: str


class MigrateBody(BaseModel):
    source_path: str
    password: str | None = None


class CookieBody(BaseModel):
    cookie: str
    group: str = "Default"


class CredentialsBody(BaseModel):
    username: str
    password: str
    group: str = "Default"


class LaunchBody(BaseModel):
    place_id: int | None = None
    job_id: str | None = None
    follow_username: str | None = None
    vip_link: str | None = None


class BatchLaunchBody(BaseModel):
    user_ids: list[int]
    place_id: int | None = None
    job_id: str | None = None
    follow_username: str | None = None
    vip_link: str | None = None


class AutoDetectBody(BaseModel):
    username: str


class EditBody(BaseModel):
    alias: str | None = None
    group: str | None = None
    description: str | None = None


class BackupBody(BaseModel):
    password: str
    path: str


class PresetBody(BaseModel):
    name: str
    place_id: int | None = None
    job_id: str | None = None
    follow_username: str | None = None


# ------------------------------------------------------------------ vault routes
@app.get("/api/health")
async def health() -> dict:
    return {"app": APP_NAME, "version": __version__, "ok": True}


@app.get("/api/vault/status")
async def vault_status() -> dict:
    return vault_state()


@app.post("/api/vault/create")
async def vault_create(body: PasswordBody) -> dict:
    try:
        vault.create(body.password)
    except (VaultError, BadPassword) as exc:
        return {"ok": False, "error": str(exc)}
    await emit_state()
    await emit_accounts()
    await toast("Vault created and unlocked.", "success")
    return {"ok": True, **vault_state()}


@app.post("/api/vault/unlock")
async def vault_unlock(body: PasswordBody) -> dict:
    try:
        vault.unlock(body.password)
    except (BadPassword, VaultError) as exc:
        return {"ok": False, "error": str(exc)}
    await emit_state()
    await emit_accounts()
    await toast(f"Unlocked — {vault.account_count} account(s).", "success")
    return {"ok": True, **vault_state()}


@app.post("/api/vault/lock")
async def vault_lock() -> dict:
    vault.lock()
    await emit_state()
    await emit_accounts()
    return {"ok": True, **vault_state()}


@app.get("/api/accounts")
async def list_accounts() -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    return {"ok": True, "accounts": [a.public_dict() for a in vault.accounts()]}


# ----------------------------------------------------------------- migration
@app.get("/api/migrate/detect")
async def migrate_detect(source_path: str) -> dict:
    return {"ok": True, "mode": migrate.detect_mode(Path(source_path))}


@app.post("/api/migrate")
async def migrate_legacy(body: MigrateBody) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Unlock the vault before importing."}
    try:
        accounts, mode = migrate.load_legacy_accounts(body.source_path, body.password)
    except migrate.PasswordRequired:
        return {"ok": False, "error": "password_required"}
    except migrate.MigrationError as exc:
        return {"ok": False, "error": str(exc)}
    added = vault.import_accounts(accounts)
    await emit_state()
    await emit_accounts()
    skipped = len(accounts) - added
    await toast(
        f"Imported {added} account(s)"
        + (f" ({skipped} already present)" if skipped else "")
        + f" from a {mode}-mode vault.",
        "success",
    )
    return {"ok": True, "found": len(accounts), "added": added, "mode": mode}


# ------------------------------------------------------------- add accounts
async def _store_validated(cookie: str, group: str) -> dict:
    info = await roblox.validate_cookie(cookie)
    if not info:
        return {"ok": False, "error": "That cookie is invalid or expired."}
    acc = vault.get(info["user_id"]) or Account(user_id=info["user_id"])
    acc.username = info["username"]
    acc.display_name = info["display_name"]
    acc.cookie = cookie
    acc.cookie_valid = True
    acc.last_validated = time.time()
    if group and group != "Default":
        acc.group = group
    if not acc.browser_tracker_id:
        acc.browser_tracker_id = launcher.new_browser_tracker_id()
    try:
        avatars = await roblox.get_avatars([acc.user_id])
        if acc.user_id in avatars:
            acc.avatar_url = avatars[acc.user_id]
    except RobloxError:
        pass
    vault.upsert(acc)
    await emit_accounts()
    await toast(f"Added {acc.label}.", "success")
    return {"ok": True, "user_id": acc.user_id, "username": acc.username}


@app.post("/api/accounts/add_cookie")
async def add_cookie(body: CookieBody) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    cookie = body.cookie.strip()
    if not cookie:
        return {"ok": False, "error": "Paste a .ROBLOSECURITY cookie."}
    try:
        return await _store_validated(cookie, body.group)
    except RobloxError as exc:
        return {"ok": False, "error": str(exc)}


@app.post("/api/accounts/add_credentials")
async def add_credentials(body: CredentialsBody) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}

    async def status(msg: str) -> None:
        await bus.broadcast({"type": "login_status", "message": msg})

    async def run() -> None:
        try:
            cookie = await login_with_credentials(
                body.username, body.password, on_status=status
            )
            await status("Validating session…")
            result = await _store_validated(cookie, body.group)
            if not result.get("ok"):
                await toast(result.get("error", "Login failed."), "error")
            await bus.broadcast({"type": "login_done", "ok": result.get("ok", False)})
        except LoginUnavailable as exc:
            await toast(str(exc), "error")
            await bus.broadcast({"type": "login_done", "ok": False, "error": str(exc)})
        except (LoginError, RobloxError) as exc:
            await toast(f"Login failed: {exc}", "error")
            await bus.broadcast({"type": "login_done", "ok": False, "error": str(exc)})

    asyncio.create_task(run())
    return {"ok": True, "started": True}


# ------------------------------------------------------------------- launch
async def _resolve_target(body: LaunchBody) -> dict:
    """Turn launch options into kwargs for launcher.build_launch_uri.

    Priority: VIP link → explicit Place ID (+ optional Job ID, so an auto-detected
    exact server wins) → follow-username. This lets Auto-Detect (which fills
    Place+Job) join the exact server, while a bare username follows the user.
    """
    if body.vip_link:
        place_id, link_code = launcher.parse_private_link(body.vip_link)
        if not (place_id and link_code):
            raise RobloxError("Couldn't read that VIP/private-server link.")
        return {"place_id": place_id, "link_code": link_code}
    if body.place_id:
        return {"place_id": body.place_id, "job_id": (body.job_id or None)}
    if body.follow_username:
        target = await roblox.lookup_username(body.follow_username)
        if not target:
            raise RobloxError(f"No Roblox user named '{body.follow_username}'.")
        return {"place_id": 0, "follow_user_id": target["user_id"]}
    raise RobloxError("Enter a username, a Place ID, or a VIP link.")


async def _launch_account(acc: Account, target: dict) -> None:
    if not acc.cookie:
        raise RobloxError(f"{acc.label} has no saved cookie — re-add it.")
    _prelaunch(acc)
    ticket = await roblox.get_auth_ticket(acc.cookie)
    build = dict(target)
    place_id = build.pop("place_id")
    folder = (config.roblox_folder or "").strip()
    if folder:
        exe = launcher.player_exe_in_folder(folder)
        if not exe:
            raise RuntimeError(f"No RobloxPlayerBeta.exe found in: {folder}")
        launcher.launch_via_exe(
            exe, ticket, place_id, browser_tracker_id=acc.browser_tracker_id, **build
        )
    else:
        uri = launcher.build_launch_uri(
            ticket, place_id, browser_tracker_id=acc.browser_tracker_id, **build
        )
        launcher.shell_launch(uri)
    acc.last_used = time.time()
    acc.last_launch = dict(target)  # remembered for the watcher's relaunch
    vault.save()


def _prelaunch(acc: Account) -> None:
    """Shared pre-launch side effects (dup-kill, privacy, multi-instance, FPS)."""
    if not acc.browser_tracker_id:
        acc.browser_tracker_id = launcher.new_browser_tracker_id()
    # ALWAYS hold Roblox's singleton lock before launching, so multiple clients
    # can coexist. Without this the previous client closes when the next opens.
    # (This is the whole point of a multi-Roblox launcher; the settings toggle
    # only controls whether the lock is also held at startup.)
    singleton.acquire()
    if config.close_existing_on_launch:
        process.kill_by_tracker(acc.browser_tracker_id)
    if config.privacy_mode:
        launcher.clear_roblox_cookies()
    if config.fps_unlock_enabled:
        fps.apply_fps(config.fps_cap)


async def _launch_account_home(acc: Account) -> None:
    """Open Roblox to the home screen as this account (no game join)."""
    if not acc.cookie:
        raise RobloxError(f"{acc.label} has no saved cookie — re-add it.")
    _prelaunch(acc)
    ticket = await roblox.get_auth_ticket(acc.cookie)
    folder = (config.roblox_folder or "").strip()
    if folder:
        exe = launcher.player_exe_in_folder(folder)
        if not exe:
            raise RuntimeError(f"No RobloxPlayerBeta.exe found in: {folder}")
        launcher.launch_via_exe_home(exe, ticket)
    else:
        launcher.shell_launch(
            launcher.build_app_launch_uri(ticket, browser_tracker_id=acc.browser_tracker_id)
        )
    acc.last_used = time.time()
    acc.last_launch = {"home": True}  # watcher relaunches back to home
    vault.save()


async def _relaunch(acc: Account) -> None:
    """Relaunch an account to wherever it last went (home or a game)."""
    if acc.last_launch and acc.last_launch.get("home"):
        await _launch_account_home(acc)
    elif acc.last_launch:
        await _launch_account(acc, dict(acc.last_launch))


@app.post("/api/accounts/{user_id}/launch_home")
async def launch_account_home(user_id: int) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    acc = vault.get(user_id)
    if not acc:
        return {"ok": False, "error": "Unknown account"}
    try:
        await _launch_account_home(acc)
    except (RobloxError, AuthError, RateLimited, RuntimeError) as exc:
        await toast(f"Launch failed: {exc}", "error")
        return {"ok": False, "error": str(exc)}
    await toast(f"Opening Roblox home as {acc.label}…", "success")
    await emit_accounts()
    return {"ok": True}


@app.post("/api/accounts/{user_id}/launch")
async def launch_account(user_id: int, body: LaunchBody) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    acc = vault.get(user_id)
    if not acc:
        return {"ok": False, "error": "Unknown account"}
    try:
        target = await _resolve_target(body)
        await _launch_account(acc, target)
    except (RobloxError, AuthError, RateLimited, RuntimeError) as exc:
        await toast(f"Launch failed: {exc}", "error")
        return {"ok": False, "error": str(exc)}
    await toast(f"Launching {acc.label}…", "success")
    await emit_accounts()
    return {"ok": True}


@app.post("/api/autodetect")
async def autodetect(body: AutoDetectBody) -> dict:
    """Resolve a username → the game (Place ID) + server (Job ID) they're in now."""
    if not vault.unlocked:
        return {"ok": False, "error": "Unlock the vault first."}
    cookie = _first_cookie()
    if not cookie:
        return {"ok": False, "error": "Add at least one account first (needed to query Roblox)."}
    name = body.username.strip()
    if not name:
        return {"ok": False, "error": "Type a username to detect."}
    try:
        target = await roblox.lookup_username(name)
        if not target:
            return {"ok": False, "error": f"No Roblox user named '{name}'."}
        pres = await roblox.get_presences(cookie, [target["user_id"]])
    except (RobloxError, AuthError, RateLimited) as exc:
        return {"ok": False, "error": str(exc)}
    p = pres.get(target["user_id"]) or {}
    place_id = p.get("place_id")
    if p.get("type") != 2 or not place_id:  # not In Game, or activity hidden
        return {
            "ok": False,
            "activity_hidden": True,
            "error": (
                f"Couldn't find a game for {target['username']}. They're either not in "
                "a game right now, or their activity is hidden. Check that the user has "
                "\"Who can see my activity/join\" turned ON in Roblox privacy settings — "
                "Hyperion can't scan a user who has their activity turned off."
            ),
        }
    return {
        "ok": True,
        "username": target["username"],
        "place_id": place_id,
        "job_id": p.get("game_id"),
        "location": p.get("last_location", ""),
        "server_hidden": p.get("game_id") is None,
    }


@app.post("/api/accounts/{user_id}/open_chrome")
async def open_chrome(user_id: int) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    acc = vault.get(user_id)
    if not acc:
        return {"ok": False, "error": "Unknown account"}
    if not acc.cookie:
        return {"ok": False, "error": f"{acc.label} has no saved cookie."}

    async def run() -> None:
        await toast(f"Opening {acc.label} in Chrome…", "info")
        try:
            await chrome.open_in_chrome(acc.username or str(acc.user_id), acc.cookie)
            await toast(f"Opened {acc.label} in Chrome.", "success")
        except chrome.ChromeUnavailable as exc:
            await toast(str(exc), "error")
        except chrome.ChromeError as exc:
            await toast(f"Chrome failed: {exc}", "error")

    asyncio.create_task(run())
    return {"ok": True, "started": True}


async def _wait_until_in_job(acc: Account, job: str, timeout: float) -> bool:
    """Poll the account's own presence until it's in the target Job ID.

    Returns True if the account is In Game in ``job`` (or in-game where the server
    isn't exposed by presence), False if the timeout elapses.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        await asyncio.sleep(3)
        try:
            pres = await roblox.get_presences(acc.cookie, [acc.user_id])
        except RobloxError:
            continue
        p = pres.get(acc.user_id)
        if p and p.get("type") == 2:  # In Game
            gid = p.get("game_id")
            if gid == job or gid is None:
                return True
    return False


async def _launch_with_retry(acc: Account, target: dict, job: str) -> bool:
    """Launch, then retry the SAME Job ID if the server was full / not joined."""
    max_r = max(0, int(config.join_max_retries or 0))
    interval = max(2, int(config.join_retry_interval_secs or 5))
    for attempt in range(max_r + 1):
        await _launch_account(acc, dict(target))
        await bus.broadcast(
            {"type": "launch_progress", "label": acc.label,
             "status": f"joining… (try {attempt + 1})"}
        )
        if await _wait_until_in_job(acc, job, timeout=max(interval, 12)):
            return True
        if attempt < max_r:
            await toast(
                f"{acc.label}: server full / not joined — retrying same server "
                f"({attempt + 1}/{max_r})…",
                "info",
            )
            await asyncio.sleep(interval)
    await toast(f"{acc.label}: couldn't get into the target server after {max_r} retries.", "error")
    return False


@app.post("/api/accounts/launch_batch")
async def launch_batch(body: BatchLaunchBody) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}

    async def run() -> None:
        single = LaunchBody(
            place_id=body.place_id, job_id=body.job_id,
            follow_username=body.follow_username, vip_link=body.vip_link,
        )
        try:
            target_base = await _resolve_target(single)
        except RobloxError as exc:
            await toast(str(exc), "error")
            return
        # Engage the multi-instance lock up front so every bot in this batch can
        # stay open at the same time. Warn if Roblox is already running, since
        # those pre-existing clients aren't covered by a lock acquired now.
        if len(body.user_ids) > 1:
            pre_existing = process.instance_count()
            singleton.acquire()
            if pre_existing > 0:
                await toast(
                    f"{pre_existing} Roblox client(s) already open — close them first "
                    "if bots start closing each other.",
                    "info",
                )
        target_job = target_base.get("job_id")
        total = len(body.user_ids)
        launched = 0
        for i, uid in enumerate(body.user_ids):
            acc = vault.get(uid)
            if not acc:
                continue
            try:
                if target_job and config.join_retry_enabled:
                    ok = await _launch_with_retry(acc, dict(target_base), target_job)
                else:
                    await _launch_account(acc, dict(target_base))
                    ok = True
                if ok:
                    launched += 1
                await bus.broadcast(
                    {"type": "launch_progress", "done": launched, "total": total, "label": acc.label}
                )
            except (RobloxError, RuntimeError) as exc:
                await toast(f"{acc.label}: {exc}", "error")
            if i < total - 1 and config.launch_delay_secs > 0:
                await asyncio.sleep(config.launch_delay_secs)
        await toast(f"Batch launch complete — {launched}/{total} joined.", "success")
        await emit_accounts()

    asyncio.create_task(run())
    return {"ok": True, "started": True, "count": len(body.user_ids)}


# ------------------------------------------------------ validate / refresh
@app.post("/api/accounts/{user_id}/validate")
async def validate_account(user_id: int) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    acc = vault.get(user_id)
    if not acc:
        return {"ok": False, "error": "Unknown account"}
    info = await roblox.validate_cookie(acc.cookie)
    acc.cookie_valid = bool(info)
    acc.last_validated = time.time()
    if info:
        acc.username = info["username"]
        acc.display_name = info["display_name"]
    vault.save()
    await emit_accounts()
    await toast(
        f"{acc.label}: cookie {'valid' if acc.cookie_valid else 'EXPIRED'}.",
        "success" if acc.cookie_valid else "error",
    )
    return {"ok": True, "valid": acc.cookie_valid}


@app.post("/api/accounts/refresh")
async def refresh_accounts() -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    accounts = vault.accounts()
    uids = [a.user_id for a in accounts]
    cookie = _first_cookie()
    try:
        presences = await roblox.get_presences(cookie, uids) if cookie else {}
        avatars = await roblox.get_avatars(uids)
    except RobloxError as exc:
        return {"ok": False, "error": str(exc)}
    for a in accounts:
        if a.user_id in presences:
            p = presences[a.user_id]
            a.last_presence = {
                "type": p["type"], "place_id": p.get("place_id"),
                "game_id": p.get("game_id"), "last_location": p.get("last_location", ""),
            }
        if a.user_id in avatars:
            a.avatar_url = avatars[a.user_id]
    vault.save()
    await emit_accounts()
    await toast("Refreshed presence & avatars.", "success")
    return {"ok": True}


@app.patch("/api/accounts/{user_id}")
async def edit_account(user_id: int, body: EditBody) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    acc = vault.get(user_id)
    if not acc:
        return {"ok": False, "error": "Unknown account"}
    if body.alias is not None:
        acc.alias = body.alias
    if body.group is not None:
        acc.group = body.group or "Default"
    if body.description is not None:
        acc.description = body.description
    vault.save()
    await emit_accounts()
    return {"ok": True}


@app.post("/api/accounts/{user_id}/keepalive")
async def set_keepalive(user_id: int, body: dict) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    acc = vault.get(user_id)
    if not acc:
        return {"ok": False, "error": "Unknown account"}
    acc.keep_alive = bool(body.get("enabled"))
    vault.save()
    await emit_accounts()
    return {"ok": True, "keep_alive": acc.keep_alive}


# ------------------------------------------------------------- kill Roblox
@app.post("/api/roblox/kill")
async def kill_roblox() -> dict:
    n = process.kill_all()
    _optimizer.reset()
    await toast(
        f"Killed {n} Roblox client(s)." if n else "No Roblox clients were running.",
        "success" if n else "info",
    )
    await emit_config()  # refresh the "N running" pill
    return {"ok": True, "killed": n}


# --------------------------------------------------------------- FPS unlocker
@app.post("/api/fps/apply")
async def fps_apply() -> dict:
    if config.fps_unlock_enabled:
        n = fps.apply_fps(config.fps_cap)
        await toast(f"FPS unlock applied to {n} Roblox version(s) ({config.fps_cap} FPS).", "success")
    else:
        n = fps.clear_fps()
        await toast(f"FPS unlock removed from {n} Roblox version(s).", "info")
    return {"ok": True, "count": n}


# ------------------------------------------------------------- backup / export
@app.post("/api/backup/export")
async def backup_export(body: BackupBody) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    try:
        data = vault.export_encrypted(body.password)
        Path(body.path).write_bytes(data)
    except (VaultError, BadPassword) as exc:
        return {"ok": False, "error": str(exc)}
    except OSError as exc:
        return {"ok": False, "error": f"Couldn't write file: {exc}"}
    await toast(f"Exported {vault.account_count} account(s) to backup.", "success")
    return {"ok": True, "count": vault.account_count}


@app.post("/api/backup/import")
async def backup_import(body: BackupBody) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    try:
        data = Path(body.path).read_bytes()
        accounts = Vault.decrypt_export(data, body.password)
    except (VaultError, BadPassword) as exc:
        return {"ok": False, "error": str(exc)}
    except OSError as exc:
        return {"ok": False, "error": f"Couldn't read file: {exc}"}
    added = vault.import_accounts(accounts)
    await emit_accounts()
    await toast(f"Restored {added} account(s) from backup.", "success")
    return {"ok": True, "found": len(accounts), "added": added}


# ------------------------------------------------------------------ presets
@app.post("/api/presets")
async def add_preset(body: PresetBody) -> dict:
    name = body.name.strip()
    if not name:
        return {"ok": False, "error": "Preset needs a name."}
    if not body.place_id and not (body.follow_username or "").strip():
        return {"ok": False, "error": "Preset needs a Place ID or a follow username."}
    config.presets = [p for p in config.presets if p.get("name") != name]
    config.presets.append({
        "name": name,
        "place_id": body.place_id or 0,
        "job_id": body.job_id or "",
        "follow_username": (body.follow_username or "").strip(),
    })
    config.save()
    await emit_config()
    await toast(f"Saved preset '{name}'.", "success")
    return {"ok": True}


@app.delete("/api/presets/{name}")
async def delete_preset(name: str) -> dict:
    config.presets = [p for p in config.presets if p.get("name") != name]
    config.save()
    await emit_config()
    return {"ok": True}


# ------------------------------------------------------- external control API
def _check_external(key: str | None, flag: str) -> None:
    if not config.external_api_enabled:
        raise HTTPException(403, "The local control API is disabled.")
    if not config.external_api_key or key != config.external_api_key:
        raise HTTPException(401, "Invalid or missing X-API-Key.")
    if not getattr(config, flag, False):
        raise HTTPException(403, "That action is not permitted by the API settings.")


@app.get("/external/accounts")
async def ext_accounts(x_api_key: str | None = Header(default=None)) -> dict:
    _check_external(x_api_key, "external_allow_list")
    if not vault.unlocked:
        raise HTTPException(409, "Vault is locked.")
    return {
        "accounts": [
            {"user_id": a.user_id, "username": a.username, "group": a.group,
             "presence": a.last_presence.get("type", 0)}
            for a in vault.accounts()
        ]
    }


@app.post("/external/launch")
async def ext_launch(body: dict, x_api_key: str | None = Header(default=None)) -> dict:
    _check_external(x_api_key, "external_allow_launch")
    if not vault.unlocked:
        raise HTTPException(409, "Vault is locked.")
    ident = str(body.get("account", "")).strip().lower()
    acc = next(
        (a for a in vault.accounts()
         if str(a.user_id) == ident or a.username.lower() == ident),
        None,
    )
    if not acc:
        raise HTTPException(404, "Unknown account.")
    try:
        target = await _resolve_target(
            LaunchBody(place_id=body.get("place_id"), job_id=body.get("job_id"),
                       vip_link=body.get("vip_link"), follow_username=body.get("follow_username"))
        )
        await _launch_account(acc, target)
    except (RobloxError, RuntimeError) as exc:
        raise HTTPException(400, str(exc))
    await emit_accounts()
    return {"ok": True, "launched": acc.username}


@app.get("/external/cookie")
async def ext_cookie(user_id: int, x_api_key: str | None = Header(default=None)) -> dict:
    _check_external(x_api_key, "external_allow_get_cookie")
    if not vault.unlocked:
        raise HTTPException(409, "Vault is locked.")
    acc = vault.get(user_id)
    if not acc:
        raise HTTPException(404, "Unknown account.")
    return {"user_id": user_id, "cookie": acc.cookie}


@app.delete("/api/accounts/{user_id}")
async def delete_account(user_id: int) -> dict:
    if not vault.unlocked:
        return {"ok": False, "error": "Vault is locked"}
    removed = vault.remove(user_id)
    await emit_accounts()
    if removed:
        await toast("Account removed.", "info")
    return {"ok": removed}


# ------------------------------------------------------------------- config
@app.get("/api/config")
async def get_config() -> dict:
    return {"ok": True, "config": config_state()}


@app.post("/api/config")
async def set_config(body: dict) -> dict:
    known = set(Config.__dataclass_fields__)
    changed_mi = False
    touched_fps = False
    for k, v in body.items():
        if k in known:
            if k == "multi_instance" and v != config.multi_instance:
                changed_mi = True
            if k in ("fps_unlock_enabled", "fps_cap"):
                touched_fps = True
            setattr(config, k, v)
    # Generate an API key the first time the local control API is switched on.
    if config.external_api_enabled and not config.external_api_key:
        config.external_api_key = secrets.token_urlsafe(24)
    config.save()
    if changed_mi:
        singleton.set_enabled(config.multi_instance)
        if config.multi_instance:
            held = ", ".join(singleton.held_names()) or "none"
            await toast(f"Multi-instance ON (holding {held}).", "success")
            if process.instance_count() > 0:
                await toast(
                    "Roblox is already running — close ALL Roblox windows, then launch "
                    "again so the multi-instance lock applies.",
                    "error",
                )
        else:
            await toast("Multi-instance disabled.", "info")
    if touched_fps:
        if config.fps_unlock_enabled:
            fps.apply_fps(config.fps_cap)
        else:
            fps.clear_fps()
    await emit_config()
    return {"ok": True, "config": config_state()}


# ---------------------------------------------------------------------- WS
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await bus.connect(ws)
    await ws.send_json({"type": "hello", "app": APP_NAME, "version": __version__})
    await ws.send_json({"type": "state", **vault_state()})
    await ws.send_json({"type": "config", "config": config_state()})
    if vault.unlocked:
        await ws.send_json(
            {"type": "accounts", "accounts": [a.public_dict() for a in vault.accounts()]}
        )
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await bus.disconnect(ws)
    except Exception:
        with contextlib.suppress(Exception):
            await bus.disconnect(ws)


# ------------------------------------------------------------------- static UI
@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url="/app/")


_web = paths.web_dir()
if _web.is_dir():
    app.mount("/app", StaticFiles(directory=str(_web), html=True), name="web")
