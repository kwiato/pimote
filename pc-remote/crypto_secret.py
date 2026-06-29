#!/usr/bin/env python3
"""Secret handling for PC Remote.

Two independent secrets live in the config dir (default ~/.config/pc-remote):

  panel.secret      scrypt hash of the PANEL password (verifies who may use the
                    web panel). One-way — never stores the password itself.
  login.secret.enc  the WINDOWS login password, encrypted with a Fernet key
                    DERIVED FROM THE PANEL PASSWORD (scrypt). The key is never
                    written to disk, so this file is useless to anyone who can
                    read the SD card but does not know the panel password.
  api.token         a long random bearer token for automation (e.g. a Glance
                    "wake PC" tile). Stored as-is (compared in constant time).

The app caches the panel password in memory for the duration of a login
session; that is what lets the "Log in" button decrypt and type the Windows
password. After a service restart the cache is gone, so you log in to the panel
once more before that button works again.

This module is also a small CLI used by install.sh (see main())."""

import base64
import hashlib
import hmac
import os
import secrets
import sys

try:
    from cryptography.fernet import Fernet, InvalidToken
    HAVE_CRYPTO = True
except Exception:  # pragma: no cover - exercised only when the dep is missing
    HAVE_CRYPTO = False

# scrypt cost. ~16 MB / ~100 ms on a Pi Zero 2 W — fine for an interactive login.
_N, _R, _P = 2 ** 14, 8, 1
_MAXMEM = 64 * 1024 * 1024


def _scrypt(password: str, salt: bytes, dklen: int = 32) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt,
                          n=_N, r=_R, p=_P, dklen=dklen, maxmem=_MAXMEM)


# --- panel password (one-way hash, for authentication) ----------------------

def hash_panel_password(password: str) -> str:
    salt = os.urandom(16)
    dk = _scrypt(password, salt)
    return "scrypt$%s$%s" % (base64.b64encode(salt).decode(),
                             base64.b64encode(dk).decode())


def verify_panel_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, dk_b64 = stored.strip().split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        dk = base64.b64decode(dk_b64)
        calc = _scrypt(password, salt, dklen=len(dk))
        return hmac.compare_digest(calc, dk)
    except Exception:
        return False


# --- Windows password (reversible encryption, key from the panel password) --

def _fernet_key(panel_password: str, salt: bytes) -> bytes:
    return base64.urlsafe_b64encode(_scrypt(panel_password, salt))


def encrypt_windows_password(panel_password: str, win_password: str) -> str:
    if not HAVE_CRYPTO:
        raise RuntimeError("python3-cryptography is not installed")
    salt = os.urandom(16)
    token = Fernet(_fernet_key(panel_password, salt)).encrypt(
        win_password.encode("utf-8"))
    # salt on the first line, the Fernet token on the second
    return base64.b64encode(salt).decode() + "\n" + token.decode()


def decrypt_windows_password(panel_password: str, blob: str):
    """Return the Windows password, or None when the panel password is wrong
    or the blob is unreadable."""
    if not HAVE_CRYPTO:
        return None
    try:
        salt_b64, token = blob.strip().split("\n", 1)
        salt = base64.b64decode(salt_b64)
        return Fernet(_fernet_key(panel_password, salt)).decrypt(
            token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


# --- CLI used by install.sh --------------------------------------------------

def _cfg_dir() -> str:
    return os.environ.get(
        "PC_REMOTE_CFG_DIR", os.path.expanduser("~/.config/pc-remote"))


def _write_600(path: str, data: str) -> None:
    # Create with restrictive perms from the start (avoid a brief 0644 window).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    os.chmod(path, 0o600)


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    cfg = _cfg_dir()
    panel_file = os.path.join(cfg, "panel.secret")
    token_file = os.path.join(cfg, "api.token")
    win_file = os.path.join(cfg, "login.secret.enc")

    if cmd == "set-panel":
        pw = os.environ.get("PANEL_PW", "")
        if not pw:
            print("PANEL_PW not set", file=sys.stderr); return 2
        _write_600(panel_file, hash_panel_password(pw) + "\n")
        return 0

    if cmd == "verify-panel":
        pw = os.environ.get("PANEL_PW", "")
        try:
            with open(panel_file, encoding="utf-8") as f:
                stored = f.read()
        except FileNotFoundError:
            return 1
        return 0 if verify_panel_password(pw, stored) else 1

    if cmd == "set-windows":
        panel_pw = os.environ.get("PANEL_PW", "")
        win_pw = os.environ.get("WIN_PW", "")
        if not panel_pw or not win_pw:
            print("PANEL_PW and WIN_PW must be set", file=sys.stderr); return 2
        _write_600(win_file, encrypt_windows_password(panel_pw, win_pw) + "\n")
        return 0

    if cmd == "gen-token":
        # Print the token so install.sh can show it. Reuse an existing one
        # unless FORCE=1, so re-running the installer doesn't break Glance.
        if os.path.exists(token_file) and os.environ.get("FORCE") != "1":
            with open(token_file, encoding="utf-8") as f:
                print(f.read().strip())
            return 0
        token = secrets.token_urlsafe(32)
        _write_600(token_file, token + "\n")
        print(token)
        return 0

    print("usage: crypto_secret.py {set-panel|verify-panel|set-windows|gen-token}",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
