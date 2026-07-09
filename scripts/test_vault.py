"""Smoke test: vault round-trip + legacy-mode detection.

Run from the project root:
    .venv\\Scripts\\python.exe scripts\\test_vault.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hyperion_am.core import migrate
from hyperion_am.core.models import Account
from hyperion_am.core.vault import BadPassword, Vault

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
failures = 0


def check(name: str, cond: bool) -> None:
    global failures
    print(f"  [{PASS if cond else FAIL}] {name}")
    if not cond:
        failures += 1


def test_vault_roundtrip() -> None:
    print("Vault round-trip:")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "vault.dat"
        v = Vault(path)
        check("vault does not exist yet", not v.exists())

        v.create("hunter2")
        check("vault exists after create", v.exists())
        check("vault is unlocked after create", v.unlocked)

        v.upsert(Account(user_id=123, username="alice", cookie="SECRET_COOKIE_A"))
        v.upsert(Account(user_id=456, username="bob", cookie="SECRET_COOKIE_B", group="Mains"))
        check("two accounts stored", v.account_count == 2)

        # public_dict must never leak the cookie
        pub = v.get(123).public_dict()
        check("public_dict strips cookie", "cookie" not in pub and pub["has_cookie"])

        v.lock()
        check("locked clears memory", not v.unlocked and v.account_count == 0)

        # wrong password rejected
        try:
            v.unlock("wrong")
            check("wrong password rejected", False)
        except BadPassword:
            check("wrong password rejected", True)

        v.unlock("hunter2")
        check("unlock restores accounts", v.account_count == 2)
        check("cookie survived round-trip", v.get(456).cookie == "SECRET_COOKIE_B")
        check("group survived round-trip", v.get(456).group == "Mains")

        # ciphertext on disk must not contain the plaintext cookie
        blob = path.read_bytes()
        check("cookie not in ciphertext", b"SECRET_COOKIE_A" not in blob)

        v.change_password("newpass")
        v.lock()
        v.unlock("newpass")
        check("re-key works", v.account_count == 2)

        # Portable encrypted backup round-trip
        blob = v.export_encrypted("backuppw")
        restored = Vault.decrypt_export(blob, "backuppw")
        check("backup export/import round-trip", len(restored) == 2 and restored[0].cookie == "SECRET_COOKIE_A")
        try:
            Vault.decrypt_export(blob, "wrongpw")
            check("backup rejects wrong password", False)
        except BadPassword:
            check("backup rejects wrong password", True)
        check("backup blob hides cookie", b"SECRET_COOKIE_A" not in blob)


def test_legacy_detection() -> None:
    print("Legacy detection:")
    # Optionally verify password-mode detection against a real RAM AccountData.json
    # by pointing RAM_ACCOUNTDATA at it (skipped by default).
    real_env = os.environ.get("RAM_ACCOUNTDATA")
    if real_env and Path(real_env).exists():
        check("real RAM file detected as password", migrate.detect_mode(Path(real_env)) == "password")
    else:
        print("  [skip] set RAM_ACCOUNTDATA to test a real file")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "plain.json").write_text('[{"UserID":1,"Username":"x","SecurityToken":"c"}]')
        check("plaintext detected", migrate.detect_mode(d / "plain.json") == "plaintext")
        accts, mode = migrate.load_legacy_accounts(d / "plain.json")
        check("plaintext import maps account", len(accts) == 1 and accts[0].user_id == 1)
        check("plaintext mode reported", mode == "plaintext")


if __name__ == "__main__":
    test_vault_roundtrip()
    test_legacy_detection()
    print()
    if failures:
        print(f"\033[91m{failures} check(s) failed.\033[0m")
        sys.exit(1)
    print("\033[92mAll checks passed.\033[0m")
