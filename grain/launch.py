"""Opening things on Windows, shared by the built-in tools and plugins."""
import difflib
import json
import os
import re
import subprocess
import threading
import time

_notifier = print  # replaced by the app with its tray notification
_apps_lock = threading.Lock()
_apps, _apps_read_at = {}, 0.0
REFRESH_SECONDS = 60  # re-read the Start menu at most once a minute, and only when a name isn't found

# Start-menu entries that are never what someone means by "open X"
NOT_PROGRAMS = re.compile(r"\b(uninstall|uninstaller|help|readme|read me|support|documentation|manual|release notes|"
                          r"website|license|licence|changelog)\b", re.IGNORECASE)


def start_menu_apps(refresh=False):
    """Every Start-menu entry as {name: AppID}, the same list Start itself shows (Store apps included)."""
    global _apps, _apps_read_at
    with _apps_lock:
        stale = time.time() - _apps_read_at > REFRESH_SECONDS
        if not _apps or (refresh and stale):
            found = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW).stdout
            entries = json.loads(found or "[]")
            entries = [entries] if isinstance(entries, dict) else entries
            _apps = {e["Name"]: e["AppID"] for e in entries if e.get("Name") and e.get("AppID")}
            _apps_read_at = time.time()
        return dict(_apps)


def start_menu_target(name):
    """The shell:AppsFolder path for an exact Start-menu name, or None."""
    apps = start_menu_apps()
    if name not in apps:
        apps = start_menu_apps(refresh=True)  # maybe installed since Grain started
    return "shell:AppsFolder\\" + apps[name] if name in apps else None


def _simple(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def find_start_app(spoken, aliases=None):
    """The Start-menu name that best matches what someone said, or None.
    Tries, in order: aliases, the exact name, the name without spaces ("chatgpt" / "Chat GPT"),
    a name that starts with it ("gog" -> "GOG GALAXY"), one containing all its words, then a close spelling."""
    def search(apps):
        programs = {n: a for n, a in apps.items() if not NOT_PROGRAMS.search(n) and not a.lower().startswith("http")}
        simple = {_simple(n): n for n in sorted(programs, key=len, reverse=True)}  # shorter names win ties
        wanted = _simple(spoken)
        if not wanted:
            return None
        for alias, target in (aliases or {}).items():
            if _simple(alias) == wanted and _simple(target) in simple:
                return simple[_simple(target)]
        if wanted in simple:
            return simple[wanted]
        squashed = wanted.replace(" ", "")
        for key, name in simple.items():
            if key.replace(" ", "") == squashed:
                return name
        starts = [name for key, name in simple.items() if key.startswith(wanted + " ") or key.startswith(wanted)]
        if starts:
            return min(starts, key=len)
        words = set(wanted.split())
        containing = [name for key, name in simple.items() if words <= set(key.split())]
        if containing:
            return min(containing, key=len)
        close = difflib.get_close_matches(wanted, list(simple), n=1, cutoff=0.8)
        return simple[close[0]] if close else None

    return search(start_menu_apps()) or search(start_menu_apps(refresh=True))


def open_target(target):
    """Open a program, file, folder or URL, or "start:<Start-menu name>". Raises if it can't be found."""
    if target.startswith("start:"):
        resolved = start_menu_target(target[len("start:"):])
        if resolved is None:
            raise FileNotFoundError(f"couldn't find {target[len('start:'):]!r} in the Start menu")
        target = resolved
    os.startfile(target)


def notify(message):
    """Show a tray notification (from plugins, timers and other background work)."""
    _notifier(message)


def set_notifier(function):
    global _notifier
    _notifier = function
