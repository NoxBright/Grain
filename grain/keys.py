"""Program keys: every program that uses Grain's API gets a key of its own, made in the tool editor.

The owner key (api_token) belongs to the tool editor and can do everything. A program key
belongs to one program, named by its client name, and can only:
  - register and remove that program's own remote tools,
  - send requests (which can't approve anything Grain is waiting to ask you about),
  - read Grain's status and the results of its own requests.

The key is shown once, when it's made. Only its SHA-256 hash is kept, in settings.json under
api_keys, so the settings file never holds a working program key. Revoking a key also removes
the program's tools.
"""
import hashlib
import hmac
import re
import secrets
import threading
import time

from .registry import NAME_PATTERN

KEY_PATTERN = re.compile(r"^grk_([0-9a-f]{8})_[A-Za-z0-9_-]{32}$")
SAVE_LAST_USED_EVERY = 60  # seconds; last_used is for the editor's list, not worth a write per request

_lock = threading.Lock()


def _hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


def public(entry):
    """A key's details without its hash."""
    return {"id": entry["id"], "client": entry["client"], "created": entry["created"], "last_used": entry.get("last_used")}


def listing(config):
    return [public(entry) for entry in config.get("api_keys", [])]


def issue(config, client):
    """Make a key for a program. Returns (key, details); the key itself is never stored."""
    client = str(client or "").strip()
    if not NAME_PATTERN.match(client):
        raise ValueError("program names are snake_case, like my_robot")
    with _lock:
        entries = config.setdefault("api_keys", [])
        if any(entry["client"] == client for entry in entries):
            raise ValueError(f"{client} already has a key. Revoke it first to make a new one.")
        key_id = secrets.token_hex(4)
        key = f"grk_{key_id}_{secrets.token_urlsafe(24)}"
        entry = {"id": key_id, "client": client, "hash": _hash(key), "created": time.time(), "last_used": None}
        entries.append(entry)
        config.save()
    return key, public(entry)


def revoke(config, key_id):
    """Remove a key; returns its details, or None if there's no such key."""
    with _lock:
        entries = config.get("api_keys", [])
        found = next((entry for entry in entries if entry["id"] == key_id), None)
        if found is None:
            return None
        config["api_keys"] = [entry for entry in entries if entry["id"] != key_id]
        config.save()
    return public(found)


def find(config, given):
    """The key entry a presented program key belongs to, or None."""
    match = KEY_PATTERN.match(given or "")
    if not match:
        return None
    digest = _hash(given)
    for entry in config.get("api_keys", []):
        if entry["id"] == match.group(1) and hmac.compare_digest(entry["hash"], digest):
            now = time.time()
            if now - (entry.get("last_used") or 0) > SAVE_LAST_USED_EVERY:
                entry["last_used"] = now
                config.save()
            return entry
    return None
