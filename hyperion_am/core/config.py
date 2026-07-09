"""Non-secret application settings, persisted to ``config.json``.

Never holds cookies or passwords — only behavioural toggles. Lives next to the
vault in ``%APPDATA%\\HyperionAccountManager``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

from . import paths


@dataclass
class Config:
    # Hold ROBLOX_singletonMutex so multiple clients can run. OFF by default —
    # interacts with Roblox anti-cheat and may carry ban risk.
    multi_instance: bool = False
    # Clear RobloxCookies.dat before each launch (reduces account association).
    privacy_mode: bool = False
    # Minimum seconds between launches (Roblox rate-limits aggressive launching).
    launch_delay_secs: int = 3
    # Sequential-launch queue: if a specific Job ID is full, wait and retry the
    # SAME server instead of hopping to a different one.
    join_retry_enabled: bool = True
    join_retry_interval_secs: int = 5
    join_max_retries: int = 12
    # Close an account's previous instance before relaunching it.
    close_existing_on_launch: bool = True
    # Optional explicit RobloxPlayerBeta.exe path (auto-detected if empty).
    roblox_player_path: str | None = None
    # Custom Roblox folder: blank = normal protocol launch; if set, launch that
    # folder's RobloxPlayerBeta.exe directly (run older / archived builds).
    roblox_folder: str = ""
    # Watcher: auto-relaunch "keep online" accounts that fall out of game.
    watcher_enabled: bool = False
    watcher_interval_secs: int = 30
    watcher_grace_checks: int = 2
    # FPS unlocker: patch ClientAppSettings.json before launch.
    fps_unlock_enabled: bool = False
    fps_cap: int = 240
    # Saved launch presets: [{"name", "place_id", "job_id"}].
    presets: list = field(default_factory=list)
    # Local control API (OFF by default) — lets external scripts drive the app.
    external_api_enabled: bool = False
    external_api_key: str = ""
    external_allow_list: bool = True
    external_allow_launch: bool = True
    external_allow_get_cookie: bool = False  # dangerous — keep off
    # Optimizer: minimize + core-pin + RAM-trim alt Roblox clients while leaving
    # the chosen MAIN account untouched. Defaults mirror the standalone Optimizer.
    optimizer_enabled: bool = False
    optimizer_main_user_id: int | None = None  # this account is never touched
    optimizer_soft_ram_mb: int = 500           # trim alts above this working set
    optimizer_trim_interval_secs: int = 30
    optimizer_warmup_minutes: float = 0.5      # grace before an alt is throttled
    optimizer_minimize_alts: bool = True
    optimizer_bot_cores: int = 3               # CPU cores each alt is pinned to
    optimizer_kill_crashhandler: bool = True   # kill RobloxCrashHandler.exe

    @classmethod
    def load(cls) -> "Config":
        p = paths.config_path()
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8"))
                known = set(cls.__dataclass_fields__)
                return cls(**{k: v for k, v in data.items() if k in known})
            except Exception:
                pass
        return cls()

    def save(self) -> None:
        paths.config_path().write_text(json.dumps(asdict(self), indent=2), "utf-8")

    def as_dict(self) -> dict:
        return asdict(self)
