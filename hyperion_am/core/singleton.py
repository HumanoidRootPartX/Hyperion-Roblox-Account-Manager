"""Multi-instance support via Roblox's singleton kernel object.

Roblox enforces a single client by creating a named singleton object at startup;
if *we* create and hold it first, Roblox's own check is satisfied and additional
clients are allowed to launch.

Roblox **renamed** this object over time:

* older clients used a mutex named ``ROBLOX_singletonMutex``
* current clients (2025–2026) use ``ROBLOX_singletonEvent``

The bootstrappers that "no longer work" (Fishstrap/Voidstrap/old Bloxstrap)
generally only held the old name. We hold **both** — created with
``CreateMutexW`` (initial owner), which is the method the actively-maintained
MultiBloxy uses — so it works across client versions. The handles stay open for
the whole session (stashed in a module global).

**Important:** Roblox creates the object the instant a client starts, so you must
be holding it *before* launching. Workflow: close all Roblox, enable
multi-instance, then launch your accounts.

**OFF by default.** Holding the singleton interacts with Roblox's anti-cheat
(Hyperion/Byfron) and may carry ban risk. Opt-in only, on your own accounts.
"""

from __future__ import annotations

import sys

# Held first → most likely to win the race before Roblox. Current name first.
SINGLETON_NAMES = ("ROBLOX_singletonEvent", "ROBLOX_singletonMutex")

_held_handles: dict[str, int] = {}  # name -> HANDLE, kept alive for the session


def acquire() -> bool:
    """Create and hold every known singleton name. Idempotent. Returns success."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE

    acquired_any = False
    for name in SINGLETON_NAMES:
        if name in _held_handles:
            acquired_any = True
            continue
        handle = kernel32.CreateMutexW(None, True, name)  # bInitialOwner=True
        if handle:
            _held_handles[name] = handle
            acquired_any = True
    return acquired_any


def release() -> None:
    """Release all held singletons so Roblox resumes single-instance behaviour."""
    if not _held_handles:
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for name, handle in list(_held_handles.items()):
        kernel32.CloseHandle(handle)
        del _held_handles[name]


def is_held() -> bool:
    return len(_held_handles) > 0


def held_names() -> list[str]:
    return list(_held_handles)


def set_enabled(enabled: bool) -> bool:
    """Convenience toggle. Returns the resulting held-state."""
    if enabled:
        acquire()
    else:
        release()
    return is_held()
