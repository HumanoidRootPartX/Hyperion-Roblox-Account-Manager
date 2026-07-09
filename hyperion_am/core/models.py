"""Domain models for Hyperion Account Manager."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Optional

# Roblox presence type codes.
PRESENCE_LABELS = {0: "Offline", 1: "Online", 2: "In Game", 3: "In Studio"}


@dataclass
class Presence:
    """A snapshot of a Roblox account's online presence."""

    type: int = 0
    place_id: Optional[int] = None
    game_id: Optional[str] = None
    last_location: str = ""

    @property
    def label(self) -> str:
        return PRESENCE_LABELS.get(self.type, "Unknown")


@dataclass
class Account:
    """A single managed Roblox account.

    The ``cookie`` (.ROBLOSECURITY) is the secret. It only ever lives in memory
    while the vault is unlocked and inside the encrypted blob on disk — it is
    stripped from any payload sent to the UI (see :meth:`public_dict`).
    """

    user_id: int
    username: str = ""
    display_name: str = ""
    cookie: str = ""  # .ROBLOSECURITY — secret
    alias: str = ""
    group: str = "Default"
    description: str = ""
    avatar_url: str = ""
    browser_tracker_id: str = ""
    tags: list[str] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    last_presence: dict = field(default_factory=lambda: asdict(Presence()))
    cookie_valid: bool = True
    last_validated: Optional[float] = None
    last_used: Optional[float] = None
    # Watcher: when True the watcher keeps this account in-game (auto-relaunch).
    keep_alive: bool = False
    # The resolved target of the last launch, reused by the watcher to relaunch.
    last_launch: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    sort_order: int = 1_000_000

    @property
    def label(self) -> str:
        return self.alias or self.display_name or self.username or str(self.user_id)

    def public_dict(self) -> dict:
        """Serialize WITHOUT the cookie — safe to send to the UI / logs."""
        d = asdict(self)
        d.pop("cookie", None)
        d["label"] = self.label
        d["has_cookie"] = bool(self.cookie)
        return d

    def to_storage(self) -> dict:
        """Full serialization (incl. cookie) for the encrypted vault blob."""
        return asdict(self)

    @classmethod
    def from_storage(cls, d: dict) -> "Account":
        """Rebuild from a stored dict, tolerating unknown/legacy keys."""
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})
