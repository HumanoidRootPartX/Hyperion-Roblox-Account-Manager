"""In-app port of the Hyperion Optimizer.

Leaves the chosen MAIN Roblox account at full power; for every ALT client, after
a short warm-up it minimizes the window, pins it to a small rotating set of CPU
cores, and trims its working set (``EmptyWorkingSet``) whenever it climbs above a
soft RAM limit. Also kills ``RobloxCrashHandler.exe`` bloat.

Pure ctypes + psutil, so it runs in-process and works on same-user Roblox
processes without requiring the app to be elevated.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from dataclasses import dataclass

import psutil

from . import process

_SW_MINIMIZE = 6
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_SET_QUOTA = 0x0100


@dataclass
class OptConfig:
    main_btid: str = ""          # browser-tracker-id of the MAIN account (never touched)
    soft_ram_mb: int = 500
    trim_interval_secs: int = 30
    warmup_minutes: float = 0.5
    minimize_alts: bool = True
    bot_cores: int = 3
    kill_crashhandler: bool = True


def _empty_working_set(pid: int) -> bool:
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi")
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    h = kernel32.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_SET_QUOTA, False, pid)
    if not h:
        return False
    try:
        return bool(psapi.EmptyWorkingSet(h))
    finally:
        kernel32.CloseHandle(h)


def _minimize_pid(pid: int) -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lp):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            user32.ShowWindow(hwnd, _SW_MINIMIZE)
        return True

    user32.EnumWindows(enum_proc(_cb), 0)


def _cmdline(p: psutil.Process) -> str:
    with contextlib.suppress(psutil.Error):
        return " ".join(p.cmdline() or [])
    return ""


class Optimizer:
    """Stateful, tick-driven optimizer. Call :meth:`tick` on an interval."""

    def __init__(self) -> None:
        self._state: dict[int, dict] = {}   # pid -> {registered, throttled, minimized}
        self._last_trim = 0.0
        self._next_core = 0
        self.cores = os.cpu_count() or 4
        self.ram_saved_bytes = 0

    def reset(self) -> None:
        self._state.clear()
        self.ram_saved_bytes = 0

    def _resolve_main_pid(self, procs: list[psutil.Process], main_btid: str) -> int | None:
        if main_btid:
            for p in procs:
                if main_btid and main_btid in _cmdline(p):
                    return p.pid
        # Fallback: the first-opened client (lowest PID), matching the standalone tool.
        return min((p.pid for p in procs), default=None)

    def tick(self, cfg: OptConfig) -> dict:
        procs = process.roblox_processes()
        pids = {p.pid for p in procs}
        main_pid = self._resolve_main_pid(procs, cfg.main_btid)
        now = time.time()

        # register new / drop dead
        for pid in pids:
            self._state.setdefault(pid, {"registered": now, "throttled": False, "minimized": False})
        for pid in list(self._state):
            if pid not in pids:
                del self._state[pid]

        warmup = max(0.0, cfg.warmup_minutes) * 60
        core_count = max(1, self.cores)
        bot_cores = max(1, min(int(cfg.bot_cores or 1), core_count))

        for pid, st in self._state.items():
            if pid == main_pid:
                continue
            if st["throttled"]:
                # keep alts minimized if they popped back up
                if cfg.minimize_alts and st["minimized"]:
                    _minimize_pid(pid)
                continue
            if (now - st["registered"]) >= warmup:
                if cfg.minimize_alts:
                    _minimize_pid(pid)
                    st["minimized"] = True
                # rotating dedicated cores
                cores = [((self._next_core + c) % core_count) for c in range(bot_cores)]
                self._next_core = (self._next_core + bot_cores) % core_count
                with contextlib.suppress(psutil.Error, OSError):
                    psutil.Process(pid).cpu_affinity(cores)
                st["throttled"] = True

        # RAM trim on interval
        if (now - self._last_trim) >= max(5, int(cfg.trim_interval_secs or 30)):
            self._last_trim = now
            limit = int(cfg.soft_ram_mb or 500) * 1024 * 1024
            for pid, st in self._state.items():
                if pid == main_pid or not st["throttled"]:
                    continue
                with contextlib.suppress(psutil.Error):
                    before = psutil.Process(pid).memory_info().rss
                    if before > limit:
                        if _empty_working_set(pid):
                            after = psutil.Process(pid).memory_info().rss
                            if before > after:
                                self.ram_saved_bytes += before - after
            if cfg.kill_crashhandler:
                _kill_crashhandler()

        return {
            "main_pid": main_pid,
            "instances": len(self._state),
            "alts_throttled": sum(1 for s in self._state.values() if s["throttled"]),
            "ram_saved_mb": round(self.ram_saved_bytes / (1024 * 1024)),
        }


def _kill_crashhandler() -> int:
    killed = 0
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() == "robloxcrashhandler.exe":
                p.kill()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return killed
