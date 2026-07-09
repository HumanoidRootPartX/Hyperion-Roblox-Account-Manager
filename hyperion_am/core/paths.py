"""Filesystem locations for Hyperion Account Manager.

All user data lives under ``%APPDATA%\\HyperionAccountManager`` so the app keeps
its encrypted vault out of the source tree and out of any synced project folder.
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "HyperionAccountManager"


def data_dir() -> Path:
    """Return (creating if needed) the per-user data directory.

    Honours a ``HYPERION_AM_DATA`` env override (handy for portable installs or
    running a throwaway instance that won't touch your real vault).
    """
    override = os.environ.get("HYPERION_AM_DATA")
    if override:
        d = Path(override)
    else:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = Path(base) / APP_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def vault_path() -> Path:
    """Encrypted account vault."""
    return data_dir() / "vault.dat"


def config_path() -> Path:
    """Non-secret application settings (JSON, no cookies)."""
    return data_dir() / "config.json"


def web_dir() -> Path:
    """Static front-end assets shipped with the package."""
    return Path(__file__).resolve().parent.parent / "web"
