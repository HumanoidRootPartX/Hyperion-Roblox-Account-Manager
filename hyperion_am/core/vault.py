"""Encrypted account vault.

On-disk format is a small JSON envelope so it is versioned and debuggable; the
secret payload (the account list, cookies included) is sealed with AES-256-GCM
and a key derived from the user's master password via Argon2id::

    {
      "magic": "HYPAM", "version": 1,
      "kdf": {"algo": "argon2id", "salt": "<b64>", "time_cost": 3,
              "memory_cost": 65536, "parallelism": 4},
      "cipher": "aes-256-gcm",
      "nonce": "<b64>", "ciphertext": "<b64>"
    }

The derived key is held in memory only while the vault is unlocked. AES-GCM is
authenticated, so a wrong password fails the integrity check and raises
:class:`BadPassword` rather than returning garbage.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Optional

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import paths
from .models import Account

MAGIC = "HYPAM"
VERSION = 1

# Argon2id parameters — strong but snappy for an interactive desktop unlock.
ARGON_TIME = 3
ARGON_MEMORY = 64 * 1024  # KiB == 64 MiB
ARGON_PARALLELISM = 4
KEY_LEN = 32
SALT_LEN = 16
NONCE_LEN = 12


class VaultError(Exception):
    """Base error for vault operations."""


class VaultLocked(VaultError):
    """Raised when an operation requires an unlocked vault."""


class BadPassword(VaultError):
    """Raised when the supplied master password is incorrect."""


def _b64e(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _derive_key(
    password: str,
    salt: bytes,
    time_cost: int = ARGON_TIME,
    memory_cost: int = ARGON_MEMORY,
    parallelism: int = ARGON_PARALLELISM,
) -> bytes:
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=time_cost,
        memory_cost=memory_cost,
        parallelism=parallelism,
        hash_len=KEY_LEN,
        type=Type.ID,
    )


class Vault:
    """In-memory account store backed by an encrypted file on disk."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else paths.vault_path()
        self._accounts: list[Account] = []
        self._key: Optional[bytes] = None
        self._kdf: Optional[dict] = None
        self._lock = threading.RLock()
        self.unlocked = False

    # ------------------------------------------------------------------ state
    def exists(self) -> bool:
        return self.path.exists() and self.path.stat().st_size > 0

    @property
    def account_count(self) -> int:
        return len(self._accounts)

    def _require_unlocked(self) -> None:
        if not self.unlocked or self._key is None:
            raise VaultLocked("Vault is locked")

    def _new_kdf(self) -> tuple[dict, bytes]:
        salt = secrets.token_bytes(SALT_LEN)
        kdf = {
            "algo": "argon2id",
            "salt": _b64e(salt),
            "time_cost": ARGON_TIME,
            "memory_cost": ARGON_MEMORY,
            "parallelism": ARGON_PARALLELISM,
        }
        return kdf, salt

    # -------------------------------------------------------------- lifecycle
    def create(self, password: str) -> None:
        """Create a brand-new empty vault and leave it unlocked."""
        if not password:
            raise BadPassword("Master password cannot be empty")
        if self.exists():
            raise VaultError("A vault already exists at this location")
        with self._lock:
            kdf, salt = self._new_kdf()
            self._kdf = kdf
            self._key = _derive_key(password, salt)
            self._accounts = []
            self.unlocked = True
            self.save()

    def unlock(self, password: str) -> None:
        """Decrypt the vault into memory using ``password``."""
        if not self.exists():
            raise VaultError("No vault file to unlock")
        env = json.loads(self.path.read_text("utf-8"))
        if env.get("magic") != MAGIC:
            raise VaultError("Not a Hyperion vault file")
        kdf = env["kdf"]
        key = _derive_key(
            password,
            _b64d(kdf["salt"]),
            int(kdf.get("time_cost", ARGON_TIME)),
            int(kdf.get("memory_cost", ARGON_MEMORY)),
            int(kdf.get("parallelism", ARGON_PARALLELISM)),
        )
        try:
            plaintext = AESGCM(key).decrypt(
                _b64d(env["nonce"]), _b64d(env["ciphertext"]), None
            )
        except Exception as exc:  # InvalidTag etc.
            raise BadPassword("Incorrect master password") from exc
        data = json.loads(plaintext.decode("utf-8"))
        with self._lock:
            self._accounts = [Account.from_storage(a) for a in data.get("accounts", [])]
            self._kdf = kdf
            self._key = key
            self.unlocked = True

    def lock(self) -> None:
        """Drop the key and accounts from memory."""
        with self._lock:
            self._key = None
            self._accounts = []
            self.unlocked = False

    def save(self) -> None:
        """Encrypt and atomically write the current account list to disk."""
        with self._lock:
            if not self.unlocked or self._key is None:
                raise VaultLocked("Cannot save a locked vault")
            payload = json.dumps(
                {"accounts": [a.to_storage() for a in self._accounts]},
                ensure_ascii=False,
            ).encode("utf-8")
            nonce = secrets.token_bytes(NONCE_LEN)
            ciphertext = AESGCM(self._key).encrypt(nonce, payload, None)
            env = {
                "magic": MAGIC,
                "version": VERSION,
                "kdf": self._kdf,
                "cipher": "aes-256-gcm",
                "nonce": _b64e(nonce),
                "ciphertext": _b64e(ciphertext),
                "saved_at": time.time(),
            }
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(env), "utf-8")
            os.replace(tmp, self.path)  # atomic on Windows + POSIX

    def change_password(self, new_password: str) -> None:
        """Re-key the vault under a new master password."""
        self._require_unlocked()
        if not new_password:
            raise BadPassword("Master password cannot be empty")
        with self._lock:
            kdf, salt = self._new_kdf()
            self._kdf = kdf
            self._key = _derive_key(new_password, salt)
            self.save()

    # ------------------------------------------------------------ backup I/O
    def export_encrypted(self, password: str) -> bytes:
        """Return a portable, password-encrypted backup of all accounts.

        Same envelope as the vault file, so a backup is itself a valid vault and
        can be restored on any machine with the chosen password.
        """
        self._require_unlocked()
        if not password:
            raise BadPassword("Backup password cannot be empty")
        kdf, salt = self._new_kdf()
        key = _derive_key(password, salt)
        payload = json.dumps(
            {"accounts": [a.to_storage() for a in self._accounts]}, ensure_ascii=False
        ).encode("utf-8")
        nonce = secrets.token_bytes(NONCE_LEN)
        ciphertext = AESGCM(key).encrypt(nonce, payload, None)
        env = {
            "magic": MAGIC, "version": VERSION, "kdf": kdf, "cipher": "aes-256-gcm",
            "nonce": _b64e(nonce), "ciphertext": _b64e(ciphertext),
            "exported_at": time.time(),
        }
        return json.dumps(env).encode("utf-8")

    @staticmethod
    def decrypt_export(data: bytes, password: str) -> list[Account]:
        """Decrypt a backup (or any Hyperion vault file) into a list of accounts."""
        env = json.loads(data.decode("utf-8"))
        if env.get("magic") != MAGIC:
            raise VaultError("Not a Hyperion backup file")
        kdf = env["kdf"]
        key = _derive_key(
            password, _b64d(kdf["salt"]),
            int(kdf.get("time_cost", ARGON_TIME)),
            int(kdf.get("memory_cost", ARGON_MEMORY)),
            int(kdf.get("parallelism", ARGON_PARALLELISM)),
        )
        try:
            plaintext = AESGCM(key).decrypt(
                _b64d(env["nonce"]), _b64d(env["ciphertext"]), None
            )
        except Exception as exc:
            raise BadPassword("Incorrect backup password") from exc
        data2 = json.loads(plaintext.decode("utf-8"))
        return [Account.from_storage(a) for a in data2.get("accounts", [])]

    # ---------------------------------------------------------------- accounts
    def accounts(self) -> list[Account]:
        self._require_unlocked()
        return list(self._accounts)

    def get(self, user_id: int) -> Optional[Account]:
        return next((a for a in self._accounts if a.user_id == user_id), None)

    def upsert(self, account: Account) -> None:
        """Insert a new account or replace the existing one with the same id."""
        self._require_unlocked()
        with self._lock:
            existing = self.get(account.user_id)
            if existing is not None:
                self._accounts[self._accounts.index(existing)] = account
            else:
                account.sort_order = len(self._accounts)
                self._accounts.append(account)
            self.save()

    def remove(self, user_id: int) -> bool:
        self._require_unlocked()
        with self._lock:
            before = len(self._accounts)
            self._accounts = [a for a in self._accounts if a.user_id != user_id]
            changed = len(self._accounts) < before
            if changed:
                self.save()
            return changed

    def import_accounts(self, incoming: list[Account]) -> int:
        """Merge a batch of accounts. Returns the count of *new* accounts added.

        Existing accounts (matched by ``user_id``) get their cookie and profile
        details refreshed but keep their local metadata (alias, group, tags).
        """
        self._require_unlocked()
        added = 0
        with self._lock:
            by_id = {a.user_id: a for a in self._accounts}
            for acc in incoming:
                cur = by_id.get(acc.user_id)
                if cur is None:
                    acc.sort_order = len(self._accounts)
                    self._accounts.append(acc)
                    by_id[acc.user_id] = acc
                    added += 1
                else:
                    cur.cookie = acc.cookie or cur.cookie
                    cur.username = acc.username or cur.username
                    cur.display_name = acc.display_name or cur.display_name
                    if acc.alias and not cur.alias:
                        cur.alias = acc.alias
                    if acc.group and acc.group != "Default":
                        cur.group = acc.group
                    if acc.description and not cur.description:
                        cur.description = acc.description
            self.save()
        return added
