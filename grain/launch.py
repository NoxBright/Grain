"""Opening things, shared by the built-in tools and plugins. Which programs are installed, and
how one is launched, differ per OS: platform.py owns that, this module owns the matching."""
import difflib
import re
import threading
import time

from . import platform

_notifier = print  # replaced by the app with its tray notification
_apps_lock = threading.Lock()
_apps, _apps_read_at = {}, 0.0
REFRESH_SECONDS = 60  # re-read the installed programs at most once a minute, and only when a name isn't found

# Installed entries that are never what someone means by "open X"
NOT_PROGRAMS = re.compile(r"\b(uninstall|uninstaller|help|readme|read me|support|documentation|manual|release notes|"
                          r"website|license|licence|changelog)\b", re.IGNORECASE)


def start_menu_apps(refresh=False):
    """Every installed program as {name: target}, the same list the desktop's own menu shows."""
    global _apps, _apps_read_at
    with _apps_lock:
        stale = time.time() - _apps_read_at > REFRESH_SECONDS
        if not _apps or (refresh and stale):
            _apps = platform.scan_applications()
            _apps_read_at = time.time()
        return dict(_apps)


def start_menu_target(name):
    """The openable target for an exact installed-program name, or None."""
    apps = start_menu_apps()
    if name not in apps:
        apps = start_menu_apps(refresh=True)  # maybe installed since Grain started
    return platform.start_menu_target(apps[name]) if name in apps else None


def _simple(text):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def find_start_app(spoken, aliases=None):
    """The installed-program name that best matches what someone said, or None.
    Tries, in order: aliases, the exact name, the name without spaces ("chatgpt" / "Chat GPT"),
    a name that starts with it, one containing all its words, then a close spelling."""
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
    """Open a program, file, folder or URL, or "start:<installed program name>". Raises if it can't be found."""
    if target.startswith("start:"):
        resolved = start_menu_target(target[len("start:"):])
        if resolved is None:
            raise FileNotFoundError(f"couldn't find {target[len('start:'):]!r} among the installed programs")
        target = resolved
    platform.open_path(target)


def notify(message):
    """Show a tray notification (from plugins, timers and other background work)."""
    _notifier(message)


def set_notifier(function):
    global _notifier
    _notifier = function
