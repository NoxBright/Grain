"""Paths and settings.

Grain's own files (the app, models, default tools) live in the install folder and are never
written to. Everything that changes lives in the user's data folder: settings, history, notes,
the log, and the user's tools, plugins and fine-tuned models. That folder is %APPDATA%\\Grain
on Windows and $XDG_DATA_HOME/Grain (default ~/.local/share/Grain) on Linux.
"""
import json
import logging
import os
import secrets
import shutil
import sys
import threading

from . import __version__

log = logging.getLogger("grain")

FROZEN = getattr(sys, "frozen", False)  # running as Grain App.exe
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = getattr(sys, "_MEIPASS", APP_DIR)  # bundled assets (UI, icon) unpack here inside the exe
APP_NAME = "Grain App"


def default_data_dir():
    """Where Grain keeps what changes about a user's setup, unless GRAIN_DATA_DIR says otherwise."""
    override = os.environ.get("GRAIN_DATA_DIR")
    if override:
        return override
    if os.environ.get("APPDATA"):  # Windows
        return os.path.join(os.environ["APPDATA"], "Grain")
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(data_home, "Grain")


DATA_DIR = default_data_dir()
CONFIG_PATH = os.path.join(DATA_DIR, "settings.json")
LOG_PATH = os.path.join(DATA_DIR, "grain.log")
NOTES_PATH = os.path.join(DATA_DIR, "notes.md")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")
TOOLS_DIR = os.path.join(DATA_DIR, "tools")
PLUGINS_DIR = os.path.join(DATA_DIR, "plugins")
USER_MODELS_DIR = os.path.join(DATA_DIR, "models")

MODELS_DIR = os.path.join(APP_DIR, "models")
NEEDLE_WEIGHTS = os.path.join(MODELS_DIR, "needle3.cact")
WHISTLE_WEIGHTS = os.path.join(MODELS_DIR, "whistle.cact")
# Default tools and plugins copied into the data folder: "defaults\" in a build, the repo folders from source.
DEFAULTS_DIR = os.path.join(APP_DIR, "defaults") if os.path.isdir(os.path.join(APP_DIR, "defaults")) else APP_DIR
ICON_PATH = os.path.join(BUNDLE, "assets", "grain.png")
UI_DIR = os.path.join(BUNDLE, "grain", "ui")


def missing_models():
    """The bundled model files missing before the engine can do useful work."""
    return [name for name, path in (("needle3.cact", NEEDLE_WEIGHTS), ("whistle.cact", WHISTLE_WEIGHTS))
            if not os.path.isfile(path)]

CONFIG_VERSION = 2  # bump with a step in MIGRATIONS when the settings format changes

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "setup_done": False,  # the first-run setup in the tool editor sets this
    "hotkey": "ctrl+alt+space",
    "wake_word_mode": False,
    "wake_words": ["needle", "hey needle"],
    "needle_weights": os.path.join("models", "needle3.cact"),  # a fine-tuned .cact in the data folder's models works too
    "confidence_threshold": 0.5,
    "tool_thresholds": {"lock_computer": 0.7},  # overrides a tool's own threshold
    "confirm_seconds": 30,  # how long a "confirm" tool waits for a yes
    "input_device": None,
    "silence_seconds": 0.8,
    "max_seconds": 12,
    "check_for_updates": False,  # off by default: Grain makes no network requests unless asked
    "update_url": "",  # a JSON manifest {"version", "url", "notes"} to check when enabled
    # Windows apps for the built-in open_app: spoken name -> program, file, URL or
    # "start:<Start-menu name>". Every other installed program opens through plugins\apps.py.
    "apps": {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "file explorer": "explorer.exe",
        "task manager": "taskmgr.exe",
        "settings": "ms-settings:",
        "browser": "https://duckduckgo.com",
    },
    "app_aliases": {},  # aliases for configured apps or installed Start-menu names
    "api_port": 8765,
    "api_token": None,  # the owner key, for the tool editor; generated on first run
    "api_keys": [],  # program keys made in the tool editor: {id, client, hash, created, last_used} (keys.py)
    "active_profile": "auto",  # "auto" picks by foreground app via profile_rules
    # Profile -> the tools Needle guesses between (name patterns). Phrases work in every profile;
    # profiles keep the AI's choice small, since it gets less accurate past about 10 tools.
    "profiles": {
        "default": ["open_app", "open_program", "search_web", "open_youtube", "media_control", "adjust_volume",
                    "tell_time", "set_timer", "take_note", "take_screenshot"],
        "gaming": ["open_program", "media_control", "adjust_volume", "take_screenshot", "roll_dice", "set_timer",
                   "tell_time"],
        "coding": ["open_program", "open_app", "search_web", "take_note", "set_timer", "take_screenshot", "tell_time"],
        "media": ["open_youtube", "media_control", "adjust_volume", "search_web", "open_program"],
        "system": ["open_app", "lock_computer", "battery_level", "adjust_volume", "tell_time", "take_screenshot",
                   "set_timer"],
    },
    # With active_profile "auto", the app in front picks the profile.
    # Empty by default: users can opt into foreground-app rules for their own software.
    "profile_rules": [],
    "installed_defaults": [],  # default tool/plugin files already copied, so deleted ones stay deleted
}


class Config(dict):
    """The settings dict, saved back to settings.json on save()."""

    _lock = threading.Lock()
    path = CONFIG_PATH

    def save(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            temporary = self.path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(self, handle, indent=2)
            os.replace(temporary, self.path)  # never leaves a half-written settings file


def needle_weights(config):
    """The Needle model to load: the configured file from the data folder, else the install folder."""
    name = config.get("needle_weights") or ""
    for base in (DATA_DIR, APP_DIR):
        path = os.path.join(base, name)
        if name and os.path.isfile(path):
            return path
    return NEEDLE_WEIGHTS


# ---------------------------------------------------------------- upgrades between versions

def _from_v1(config):
    """Version 1 (Grain 0.x) had no first-run setup: people upgrading have already set Grain up."""
    config["setup_done"] = True
    return config


MIGRATIONS = {1: _from_v1}  # from version -> function returning the next version's settings


def migrate(config):
    version = int(config.get("config_version") or 1)
    while version < CONFIG_VERSION:
        config = MIGRATIONS[version](config)
        version += 1
    config["config_version"] = CONFIG_VERSION
    return config


# ---------------------------------------------------------------- first run

LEGACY_FILES = {"assistant_config.json": CONFIG_PATH, "history.jsonl": HISTORY_PATH, "notes.md": NOTES_PATH}


def _migrate_legacy_folder():
    """Grain 0.x kept its files beside the exe. Copy them into the data folder once."""
    if os.path.exists(CONFIG_PATH):
        return False
    moved = False
    for name, target in LEGACY_FILES.items():
        source = os.path.join(APP_DIR, name)
        if os.path.isfile(source):
            shutil.copy2(source, target)
            moved = True
    return moved


def _install_defaults(config):
    """Copy default tools and plugins the user has never had. Ones they deleted stay deleted,
    and ones Grain no longer ships are retired: a default replaced by a built-in tool would
    otherwise linger in the data folder and clash with it on every start."""
    installed = set(config.get("installed_defaults", []))
    for folder, suffix in (("tools", ".json"), ("plugins", ".py")):
        source_dir, target_dir = os.path.join(DEFAULTS_DIR, folder), os.path.join(DATA_DIR, folder)
        if not os.path.isdir(source_dir) or os.path.abspath(source_dir) == os.path.abspath(target_dir):
            continue
        shipped = {f"{folder}/{name}" for name in os.listdir(source_dir) if name.endswith(suffix)}
        for key in sorted(installed & _retired(folder, installed, shipped)):
            installed.discard(key)
            target = os.path.join(DATA_DIR, key.replace("/", os.sep))
            if os.path.exists(target):
                log.info("retiring default %s: Grain ships it no longer", key)
                os.remove(target)
        os.makedirs(target_dir, exist_ok=True)
        for name in sorted(os.listdir(source_dir)):
            key = f"{folder}/{name}"
            if not name.endswith(suffix) or key in installed:
                continue
            target = os.path.join(target_dir, name)
            if not os.path.exists(target):
                shutil.copy2(os.path.join(source_dir, name), target)
            installed.add(key)
    config["installed_defaults"] = sorted(installed)


def _retired(folder, installed, shipped):
    """Installed defaults of this folder that are no longer shipped."""
    return {key for key in installed
            if key.startswith(folder + "/") and key not in shipped}


def load_config():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(USER_MODELS_DIR, exist_ok=True)
    upgraded = _migrate_legacy_folder()
    config = Config(json.loads(json.dumps(DEFAULT_CONFIG)))
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8-sig") as handle:
            stored = json.load(handle)
        if upgraded or "config_version" not in stored:
            stored.setdefault("config_version", 1)
        config.update(stored)
    config = Config(migrate(config))
    if not config.get("api_token"):
        config["api_token"] = secrets.token_urlsafe(24)
    _install_defaults(config)
    config["app_version"] = __version__
    config.save()  # writes defaults added since the file was made
    return config
