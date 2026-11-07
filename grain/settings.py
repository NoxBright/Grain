"""The settings people can change from the tool editor: validation, starting with the session,
update checks. Starting at login is a registry key on Windows and an autostart entry on Linux,
both behind platform.autostart_*."""
import json
import re
import urllib.request

from . import __version__, platform

NEEDS_RESTART = {"api_port"}


def _number(low, high, integer=False):
    def check(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        if not low <= value <= high:
            raise ValueError(f"must be between {low} and {high}")
        return int(value) if integer else float(value)
    return check


def _boolean(value):
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


def _hotkey(value):
    value = str(value).strip().lower()
    try:
        platform.parse_hotkey(value)
    except ValueError as error:
        raise ValueError(f"isn't a key combination Grain understands ({error})")
    if "+" not in value:
        raise ValueError("needs at least two keys, like ctrl+alt+space, so it doesn't fire while you type")
    return value


def _words(value):
    if not isinstance(value, list) or not all(isinstance(w, str) for w in value):
        raise ValueError("must be a list of words")
    words = [" ".join(w.lower().split()) for w in value if w.strip()]
    if not words:
        raise ValueError("needs at least one wake word")
    return words


def _device(value):
    if value is not None and not isinstance(value, str):
        raise ValueError("must be a microphone name, or empty for this system's default")
    return value or None


def _model(value):
    value = str(value).strip()
    if not value.endswith(".cact"):
        raise ValueError("must be a .cact model file")
    return value


def _url(value):
    """https:// only, so nothing between this PC and the server can change what the check reports;
    plain http:// is allowed for an address on this PC."""
    value = str(value).strip()
    if value and not re.match(r"^(https://|http://(127\.0\.0\.1|localhost)(:\d+)?(/|$))", value):
        raise ValueError("must start with https:// (or http:// for an address on this PC)")
    return value


SCHEMA = {
    "hotkey": _hotkey,
    "wake_word_mode": _boolean,
    "wake_words": _words,
    "confidence_threshold": _number(0.1, 0.95),
    "confirm_seconds": _number(5, 300, integer=True),
    "silence_seconds": _number(0.3, 3.0),
    "max_seconds": _number(3, 60, integer=True),
    "input_device": _device,
    "needle_weights": _model,
    "api_port": _number(1024, 65535, integer=True),
    "check_for_updates": _boolean,
    "update_url": _url,
}


def validate(changes):
    """(clean changes, {setting: problem}). Unknown settings are problems too."""
    clean, problems = {}, {}
    for key, value in changes.items():
        if key == "start_with_windows":
            try:
                clean[key] = _boolean(value)
            except ValueError as error:
                problems[key] = str(error)
            continue
        if key not in SCHEMA:
            problems[key] = "isn't a setting that can be changed here"
            continue
        try:
            clean[key] = SCHEMA[key](value)
        except ValueError as error:
            problems[key] = str(error)
    return clean, problems


def public(config):
    return {key: config.get(key) for key in SCHEMA} | {"start_with_windows": starts_with_windows()}


# ---------------------------------------------------------------- starting with the session

def starts_with_windows():
    """Named after Windows for the API's sake; on Linux it is the XDG autostart entry."""
    return platform.autostart_enabled()


def set_start_with_windows(enabled):
    """A per-user startup entry (no admin rights needed); the uninstaller removes it."""
    platform.set_autostart(enabled)


# ---------------------------------------------------------------- updates

def _version_tuple(text):
    return tuple(int(part) for part in re.findall(r"\d+", str(text))[:3]) or (0,)


def check_for_update(config, timeout=8):
    """Reads the update manifest {"version", "url", "notes"} at update_url. Only ever called when
    the user turned update checks on, or pressed "Check now"."""
    url = config.get("update_url")
    if not url:
        return {"available": False, "message": "No update address is set."}
    try:
        _url(url)  # settings saved before addresses had to be https://
    except ValueError as error:
        return {"available": False, "message": f"The update address {error}."}
    request = urllib.request.Request(url, headers={"User-Agent": f"Grain/{__version__}"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        manifest = json.loads(response.read(65536))
    latest = str(manifest.get("version", ""))
    newer = _version_tuple(latest) > _version_tuple(__version__)
    try:
        download = _url(manifest.get("url", "")) if isinstance(manifest.get("url", ""), str) else ""
    except ValueError:
        download = ""  # the page links to this, so never pass on javascript: or other schemes
    return {"available": newer, "current": __version__, "latest": latest, "url": download,
            "notes": str(manifest.get("notes", ""))[:500],
            "message": f"Grain {latest} is available." if newer else f"Grain {__version__} is up to date."}
