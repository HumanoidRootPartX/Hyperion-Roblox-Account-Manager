"""Import accounts from a legacy Roblox Account Manager (RAM) ``AccountData.json``.

RAM stores its account list as JSON, encrypted one of three ways:

1. **Password mode** — a 64-byte ASCII header, then ``salt(16) | nonce(24) |
   libsodium SecretBox``. The key is derived from the user's RAM master password
   with Argon2 (libsodium ``crypto_pwhash``). libsodium-net's ``StrengthArgon``
   maps to non-obvious ops/mem limits and defaults to Argon2**i**, so rather than
   hard-code one guess we try a small set of known parameter combos and let the
   authenticated SecretBox tell us which one is right (a wrong key fails the MAC
   cleanly — there is no risk of a false positive).
2. **DPAPI mode** — a raw ``CryptProtectData`` blob (machine-bound). Decrypted
   with the fixed entropy RAM compiles in. Only works on the PC that wrote it.
3. **Plaintext** — used when the user opts out of encryption. Just UTF-8 JSON.

The decrypted JSON is a list of RAM ``Account`` objects which we map onto our own
:class:`~hyperion_am.core.models.Account`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import Account

# The 64-byte header libsodium password-mode files begin with.
RAM_HEADER = bytes(
    [
        82, 111, 98, 108, 111, 120, 32, 65, 99, 99, 111, 117, 110, 116, 32, 77,
        97, 110, 97, 103, 101, 114, 32, 99, 114, 101, 97, 116, 101, 100, 32, 98,
        121, 32, 105, 99, 51, 119, 48, 108, 102, 50, 50, 32, 64, 32, 103, 105,
        116, 104, 117, 98, 46, 99, 111, 109, 32, 46, 46, 46, 46, 46, 46, 46,
    ]
)

# Fixed entropy RAM passes to DPAPI ("ROBLOX ACCOUNT MANAGER | :) | BROUGHT TO YOU BUY ic3w0lf").
DPAPI_ENTROPY = bytes(
    [
        0x52, 0x4F, 0x42, 0x4C, 0x4F, 0x58, 0x20, 0x41, 0x43, 0x43, 0x4F, 0x55,
        0x4E, 0x54, 0x20, 0x4D, 0x41, 0x4E, 0x41, 0x47, 0x45, 0x52, 0x20, 0x7C,
        0x20, 0x3A, 0x29, 0x20, 0x7C, 0x20, 0x42, 0x52, 0x4F, 0x55, 0x47, 0x48,
        0x54, 0x20, 0x54, 0x4F, 0x20, 0x59, 0x4F, 0x55, 0x20, 0x42, 0x55, 0x59,
        0x20, 0x69, 0x63, 0x33, 0x77, 0x30, 0x6C, 0x66,
    ]
)

_SALT_LEN = 16
_NONCE_LEN = 24

# (opslimit, memlimit_bytes) combos to try for libsodium password mode, in
# rough order of likelihood. Covers libsodium-net's StrengthArgon mappings
# (Moderate=6/128MiB, Medium=3/256MiB, Interactive=4/32MiB, Sensitive=8/512MiB)
# plus libsodium's own native MODERATE constants.
_ARGON_CANDIDATES = [
    (6, 128 * 1024 * 1024),
    (3, 256 * 1024 * 1024),
    (4, 32 * 1024 * 1024),
    (8, 512 * 1024 * 1024),
    (2, 64 * 1024 * 1024),
]


class MigrationError(Exception):
    """Raised when a legacy vault cannot be read."""


class PasswordRequired(MigrationError):
    """Raised when the file is password-mode but no password was supplied."""


def detect_mode(path: Path) -> str:
    """Return ``"password" | "dpapi" | "plaintext" | "empty" | "missing"``."""
    path = Path(path)
    if not path.exists():
        return "missing"
    data = path.read_bytes()
    if not data:
        return "empty"
    if data[: len(RAM_HEADER)] == RAM_HEADER:
        return "password"
    stripped = data.lstrip()
    if stripped[:1] in (b"[", b"{"):
        return "plaintext"
    return "dpapi"


def _decrypt_password_mode(data: bytes, password: str) -> bytes:
    import nacl.pwhash
    import nacl.secret
    from nacl.exceptions import CryptoError

    body = data[len(RAM_HEADER):]
    salt = body[:_SALT_LEN]
    nonce = body[_SALT_LEN : _SALT_LEN + _NONCE_LEN]
    ciphertext = body[_SALT_LEN + _NONCE_LEN :]
    pw = password.encode("utf-8")

    last_err: Optional[Exception] = None
    # RAM defaults to Argon2i; try it first, then Argon2id, across each combo.
    for kdf in (nacl.pwhash.argon2i.kdf, nacl.pwhash.argon2id.kdf):
        for ops, mem in _ARGON_CANDIDATES:
            try:
                key = kdf(nacl.secret.SecretBox.KEY_SIZE, pw, salt,
                          opslimit=ops, memlimit=mem)
                return nacl.secret.SecretBox(key).decrypt(ciphertext, nonce)
            except CryptoError as exc:  # wrong key/params → MAC fails, try next
                last_err = exc
                continue
    raise BadPasswordOrParams(
        "Could not decrypt the password-mode vault — the master password is "
        "likely wrong (tried all known Argon2 parameter sets)."
    ) from last_err


class BadPasswordOrParams(MigrationError):
    """Password-mode decryption failed for every candidate parameter set."""


def _decrypt_dpapi(data: bytes) -> bytes:
    try:
        import win32crypt
    except ImportError as exc:  # pragma: no cover - non-Windows
        raise MigrationError("DPAPI decryption requires pywin32 on Windows") from exc
    try:
        _desc, out = win32crypt.CryptUnprotectData(data, DPAPI_ENTROPY, None, None, 0)
    except Exception as exc:
        raise MigrationError(
            "DPAPI decryption failed — this file is machine-bound and can only "
            "be migrated on the PC that created it."
        ) from exc
    return out


def _map_old_account(o: dict) -> Optional[Account]:
    """Map a RAM account dict onto our Account model. Skips entries with no id."""
    try:
        user_id = int(o.get("UserID") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if user_id <= 0:
        return None
    fields = o.get("Fields") or {}
    return Account(
        user_id=user_id,
        username=(o.get("Username") or "").strip(),
        cookie=(o.get("SecurityToken") or "").strip(),
        alias=(o.get("Alias") or "").strip(),
        description=(o.get("Description") or "").strip(),
        group=(o.get("Group") or "Default").strip() or "Default",
        browser_tracker_id=str(o.get("BrowserTrackerID") or ""),
        fields={str(k): str(v) for k, v in fields.items()},
    )


def load_legacy_accounts(
    path: str | Path, password: Optional[str] = None
) -> tuple[list[Account], str]:
    """Read and decrypt a legacy RAM vault.

    Returns ``(accounts, mode)``. Raises :class:`PasswordRequired` if the file
    is password-mode and no password was given, or :class:`MigrationError` on
    any decryption/parse failure.
    """
    path = Path(path)
    mode = detect_mode(path)
    if mode in ("missing", "empty"):
        raise MigrationError(f"Nothing to import: source file is {mode}.")

    data = path.read_bytes()
    if mode == "password":
        if not password:
            raise PasswordRequired("This vault is password-protected.")
        plaintext = _decrypt_password_mode(data, password)
    elif mode == "dpapi":
        plaintext = _decrypt_dpapi(data)
    else:  # plaintext
        plaintext = data

    try:
        raw = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError("Decrypted data was not valid JSON.") from exc

    if not isinstance(raw, list):
        raise MigrationError("Unexpected legacy format (expected a JSON array).")

    accounts = [acc for acc in (_map_old_account(o) for o in raw) if acc is not None]
    return accounts, mode
