"""Grain App: wires the registry, engine, API and tray together."""
import argparse
import logging
import logging.handlers
import os
import sys
import tempfile
import threading
import webbrowser

from . import __version__, filetools, platform, plugins, settings
from .api import serve
from .audio import Microphone, capture_utterance, input_names, rescan_devices
from .builtin import builtin_tools
from .config import (APP_DIR, APP_NAME, DATA_DIR, HISTORY_PATH, ICON_PATH, LOG_PATH, NOTES_PATH, PLUGINS_DIR,
                     TOOLS_DIR, load_config, missing_models)
from .engine import Engine, Events
from .history import History
from .launch import set_notifier
from .profiles import TOO_MANY_TOOLS, current_profile, select_tools
from .registry import ToolRegistry
from .remote import remote_tools

log = logging.getLogger("grain")


class Grain:
    """Everything except the UI: tools from every source, the engine and the history."""

    TOO_MANY_TOOLS = TOO_MANY_TOOLS

    def __init__(self, config, notify, history_path=HISTORY_PATH):
        self.config, self.notify = config, notify
        set_notifier(notify)  # grain.notify() for plugins
        self.registry = ToolRegistry()
        self.history = History(history_path)
        self.events = Events()
        self.mic_name, self.mic_names = None, []
        self.on_profiles_changed = lambda: None  # the tray refreshes its menu here
        self.on_settings_changed = lambda changes: None  # the tray re-registers the hotkey, switches mic, ...
        self._errors = {}
        self.load_errors = []
        self._report("builtin", self.registry.replace("builtin", builtin_tools(config, notify)))
        self.reload_files()
        self.reload_plugins()
        self.engine = Engine(config, self.registry, self.history, self.events)

    def _report(self, source, errors):
        changed = errors and errors != self._errors.get(source)
        self._errors[source] = errors
        self.load_errors = [e for errs in self._errors.values() for e in errs]
        if changed:
            self.notify(f"Some {source} tools didn't load: " + "; ".join(errors))
        return errors

    def reload_files(self):
        tools, errors = filetools.load_folder(TOOLS_DIR)
        return self._report("file", errors + self.registry.replace("file", tools))

    def reload_plugins(self):
        tools, errors = plugins.load_folder(PLUGINS_DIR)
        return self._report("plugin", errors + self.registry.replace("plugin", tools))

    def set_remote(self, client, callback, specs):
        return self.registry.replace("remote", remote_tools(client, callback, specs), origin=client)

    def watch_folders(self):
        filetools.FolderWatcher(TOOLS_DIR, ".json", self.reload_files).start()
        filetools.FolderWatcher(PLUGINS_DIR, ".py", self.reload_plugins).start()

    def apply_settings(self, changes):
        """Save validated changes and apply them straight away. Returns the ones that need a restart."""
        if "start_with_windows" in changes:
            settings.set_start_with_windows(changes.pop("start_with_windows"))
        changed = {k: v for k, v in changes.items() if self.config.get(k) != v}
        self.config.update(changed)
        self.config.save()
        if "needle_weights" in changed:  # rebuild the AI with the new model on the next request
            self.engine.call(self.engine._agents.clear)
        self.on_settings_changed(changed)
        return sorted(set(changed) & settings.NEEDS_RESTART)

    def profile(self):
        return current_profile(self.config)

    def active_tools(self, profile=None):
        return select_tools(self.registry.all(), self.config, profile or self.profile())


# ---------------------------------------------------------------- tray

def make_icon(color):
    """The Grain icon with a status dot in the corner."""
    from PIL import Image, ImageDraw
    image = Image.open(ICON_PATH).convert("RGBA").resize((64, 64), Image.NEAREST)
    ImageDraw.Draw(image).ellipse((38, 38, 63, 63), fill=color, outline="white", width=3)
    return image


COLORS = {"loading": "#7a7a7a", "idle": "#2f6fbd", "wake": "#2b8a5a",
          "listening": "#d13b3b", "thinking": "#d9902b"}


class TrayApp:
    def __init__(self, config):
        import pystray
        self.pystray = pystray
        self.config = config
        self.running = True
        self.listen_now = threading.Event()
        self.mic_change = None  # (device name,) for the listener thread; None inside rescans
        self.mic_names, self.mic_name = [], None
        self.icons = {state: make_icon(color) for state, color in COLORS.items()}
        self.hotkey_handle = None
        self.grain = Grain(config, self.notify)  # before the icon: its menu reads the engine
        self.icon = pystray.Icon("grain_app", self.icons["loading"], f"{APP_NAME}: loading", menu=pystray.Menu(self.menu))
        self.grain.on_profiles_changed = self.icon.update_menu
        self.grain.on_settings_changed = self.settings_changed

    # -------------------------------------------------- menu

    def menu(self):
        item, menu = self.pystray.MenuItem, self.pystray.Menu
        hotkey = self.config["hotkey"].title()
        items = [item(f"Listen now ({hotkey})", lambda: self.listen_now.set(), default=True)]
        for pending in self.grain.engine.pending_list():
            items.append(item(f"Confirm: {pending['summary'][:60]}", self.resolve(pending["id"], True)))
            items.append(item(f"Cancel: {pending['summary'][:60]}", self.resolve(pending["id"], False)))
        items += [
            item("Wake word mode", self.toggle_wake, checked=lambda _: self.config["wake_word_mode"]),
            item("Profile", menu(lambda: [item(name, self.choose_profile(name), radio=True,
                                               checked=lambda _, name=name: self.config["active_profile"] == name)
                                          for name in ["auto", *self.config["profiles"]]])),
            item("Microphone", menu(lambda: [item(name, self.choose_mic(name), radio=True,
                                                  checked=lambda _, name=name: name == self.mic_name)
                                             for name in self.mic_names]
                                    + [menu.SEPARATOR, item("Rescan devices", self.choose_mic(None))])),
            menu.SEPARATOR,
            item("Tool editor", self.open_editor),
            item("Settings", lambda: webbrowser.open(editor_url(self.config, "?tab=settings"))),
            item("Open tools folder", lambda: self.open_path(TOOLS_DIR)),
            item("Open plugins folder", lambda: self.open_path(PLUGINS_DIR)),
            item("Open notes", lambda: self.open_path(NOTES_PATH)),
            item("Open log", lambda: self.open_path(LOG_PATH)),
            menu.SEPARATOR,
            item(f"About Grain {__version__}", lambda: self.notify(
                f"Grain {__version__}: an offline voice assistant. Speech and AI run on this PC; "
                "nothing you say leaves it.")),
            item("Quit", self.quit),
        ]
        return items

    def resolve(self, pending_id, confirm):
        def run():
            engine = self.grain.engine
            future = engine.confirm(pending_id) if confirm else engine.cancel(pending_id)
            future.add_done_callback(self.show_results)
        return run

    def choose_profile(self, name):
        def run():
            self.config["active_profile"] = name
            self.config.save()
            self.icon.update_menu()
        return run

    def choose_mic(self, name):
        """None rescans and keeps the configured mic. The listener thread does the switch."""
        return lambda: setattr(self, "mic_change", (name,))

    def open_editor(self):
        webbrowser.open(editor_url(self.config))

    def open_path(self, path):
        if not os.path.exists(path):
            if os.path.splitext(path)[1]:
                open(path, "a", encoding="utf-8").close()
            else:
                os.makedirs(path, exist_ok=True)
        platform.open_path(path)

    def toggle_wake(self):
        self.config["wake_word_mode"] = not self.config["wake_word_mode"]
        self.config.save()
        self.set_state("idle", "wake word on" if self.config["wake_word_mode"] else "ready")

    def register_hotkey(self):
        import keyboard
        if self.hotkey_handle is not None:
            keyboard.remove_hotkey(self.hotkey_handle)
        self.hotkey_handle = keyboard.add_hotkey(self.config["hotkey"], self.listen_now.set)

    def settings_changed(self, changed):
        """Apply settings saved from the tool editor without a restart."""
        if "hotkey" in changed:
            try:
                self.register_hotkey()
            except Exception as error:
                log.exception("hotkey change failed")
                self.notify(f"Couldn't use that hotkey: {error}")
        if "input_device" in changed:
            self.mic_change = (None,)  # the listener reopens the mic from the (already updated) settings
        if "wake_word_mode" in changed:
            self.set_state("idle", "wake word on" if self.config["wake_word_mode"] else "ready")
        self.icon.update_menu()

    def quit(self):
        self.running = False
        self.icon.stop()

    # -------------------------------------------------- state and feedback

    def set_state(self, state, tip=None):
        if state == "idle" and self.config["wake_word_mode"]:
            state = "wake"
        self.icon.icon = self.icons[state]
        if tip:
            self.icon.title = f"{APP_NAME}: {tip}"

    def notify(self, message):
        log.debug("notify: %s", message)  # debug only: messages can quote what you said
        try:
            self.icon.notify(message[:250], APP_NAME)
        except Exception:
            log.debug("notification failed", exc_info=True)

    def show_results(self, future):
        try:
            outcome = future.result()
            heard, results = outcome if isinstance(outcome, tuple) else ("", outcome)
            message = "\n".join(r["message"] for r in results)
        except Exception as error:
            log.exception("request failed")
            message = f"Error: {error}"
        if message:
            self.notify(message)
        self.icon.update_menu()  # confirm items come and go
        self.set_state("idle", "ready")

    # -------------------------------------------------- start-up and listening

    def run(self):
        self.icon.run(setup=self.start)

    def start(self, icon):
        icon.visible = True
        threading.Thread(target=self.worker, daemon=True).start()

    def open_mic(self, wanted, chosen=False):
        mic = Microphone(wanted)
        self.mic_names, self.mic_name = input_names(mic.sd), mic.name
        self.grain.mic_name, self.grain.mic_names = mic.name, self.mic_names
        if chosen:
            self.config["input_device"] = mic.name
            self.config.save()
        self.icon.update_menu()
        log.info("microphone open (shared mode, %d Hz)", mic.rate)
        if mic.missing:
            self.notify(f"Couldn't find microphone '{mic.missing}', using {mic.name}. "
                        "Plug it in and choose Microphone > Rescan devices.")
        return mic

    def switch_mic(self, mic):
        (wanted,) = self.mic_change
        self.mic_change = None
        mic.close()
        rescan_devices(mic.sd)
        mic = self.open_mic(self.config["input_device"] if wanted is None else wanted, chosen=wanted is not None)
        self.notify(f"Microphone: {mic.name}")
        return mic

    def start_api(self):
        """Serve on the configured port, or the next free one if another program has it (and remember that)."""
        first = self.config["api_port"]
        for port in range(first, first + 20):
            try:
                serve(self.grain, port)
            except OSError:
                continue
            if port != first:
                log.info("port %d was taken; the API moved to %d", first, port)
                self.config["api_port"] = port
                self.config.save()
            return True
        self.notify(f"The tool editor couldn't start: ports {first}-{first + 19} are all in use.")
        return False

    def worker(self):
        self.start_api()
        self.grain.watch_folders()
        self.grain.engine.ready.wait()
        if self.grain.engine.load_error:
            self.notify(f"Startup failed: {self.grain.engine.load_error}")
            return
        missing = missing_models()
        if missing:
            self.notify("Grain needs its model files: " + ", ".join(missing))
            webbrowser.open(editor_url(self.config, "?tab=model-setup"))
            return
        try:
            mic = self.open_mic(self.config["input_device"])
        except Exception as error:
            log.exception("microphone failed")
            self.notify(f"Microphone failed: {error}")
            return
        try:
            self.register_hotkey()
        except Exception as error:
            log.exception("hotkey failed")
            self.notify(f"Couldn't use the hotkey {self.config['hotkey']}: {error}. Change it in Settings.")
        log.info("ready")
        self.set_state("idle", "ready")
        if not self.config.get("setup_done"):
            webbrowser.open(editor_url(self.config, "?tab=setup"))  # first run: the setup guide
        else:
            self.notify(f"Ready. Press {self.config['hotkey']} and speak.")
        if self.config.get("check_for_updates") and self.config.get("update_url"):
            threading.Thread(target=self.check_updates, daemon=True).start()
        self.listen(mic)

    def check_updates(self):
        try:
            result = settings.check_for_update(self.config)
        except Exception:
            log.info("update check failed", exc_info=True)
            return
        if result["available"]:
            self.notify(f"{result['message']} Open Settings to download it.")

    def listen(self, mic):
        while self.running:
            if self.mic_change is not None:
                try:
                    mic = self.switch_mic(mic)
                except Exception as error:
                    log.exception("switching microphone failed")
                    self.notify(f"Couldn't switch microphone: {error}")
                continue
            if self.config["wake_word_mode"]:
                mic.open()
                self.listen_now.clear()
                samples = capture_utterance(mic, self.config, lambda: not self.running or self.mic_change is not None
                                            or not self.config["wake_word_mode"] or self.listen_now.is_set())
                if self.listen_now.is_set() or self.mic_change is not None:
                    continue  # the hotkey asked for a fresh push-to-talk capture below
                if samples is not None:
                    self.submit(samples, True, self.grain.profile())
                continue
            mic.close()
            if not self.listen_now.wait(0.2):
                continue
            self.listen_now.clear()
            self.push_to_talk(mic)

    def push_to_talk(self, mic):
        profile = self.grain.profile()  # the app in front when the hotkey was pressed
        self.set_state("listening", "listening...")
        mic.open()
        samples = capture_utterance(mic, self.config, lambda: not self.running, start_timeout=5)
        if not self.config["wake_word_mode"]:
            mic.close()
        if samples is None:
            self.set_state("idle", "ready")
            if mic.peak < 0.0005:  # digital silence, not a quiet room
                self.notify(f"No sound at all from {mic.name}. Is it connected and sending audio? "
                            "Pick another under Microphone in the tray menu.")
            else:
                self.notify("Didn't hear anything.")
            return
        self.submit(samples, False, profile)

    def submit(self, samples, require_wake, profile):
        self.set_state("thinking", "thinking...")
        self.grain.engine.hear(samples, require_wake, profile).add_done_callback(self.show_results)


# ---------------------------------------------------------------- entry point

_instance_mutex = None  # held for the life of the process


def claim_single_instance():
    """True if this is the only Grain running for this user."""
    global _instance_mutex
    _instance_mutex = platform.claim_single_instance()
    return _instance_mutex


def editor_url(config, section=""):
    return f"http://127.0.0.1:{config['api_port']}/ui/{section}#token={config['api_token']}"


class PrivateFormatter(logging.Formatter):
    """Keeps the log free of the user folder (which carries the account name): paths in messages
    and tracebacks are shortened to <grain>, <data>, <temp> and ~."""

    def __init__(self, fmt):
        super().__init__(fmt)
        home = os.path.expanduser("~")
        temp = os.environ.get("TEMP") or os.environ.get("TMPDIR") or tempfile.gettempdir()
        pairs = [(DATA_DIR, "<data>"), (APP_DIR, "<grain>"), (temp, "<temp>"), (home, "~")]
        for long_path, label in list(pairs):  # Windows also writes 8.3 short forms like C:\Users\ABCDEF~1
            short = _short_path(long_path)
            if short and short != long_path:
                pairs.append((short, label))
        self.pairs = sorted([(p, l) for p, l in pairs if p], key=lambda pair: -len(pair[0]))

    def format(self, record):
        text = super().format(record)
        for path, label in self.pairs:
            text = text.replace(path, label).replace(path.lower(), label)
        return text


def _short_path(path):
    return platform.short_path(path)


def main():
    parser = argparse.ArgumentParser(prog="grain_app", description="Grain App: offline voice tools on Needle.")
    parser.add_argument("--wav", help="transcribe and handle one WAV file, then exit")
    parser.add_argument("--text", help="handle one typed request, then exit")
    parser.add_argument("--dry-run", action="store_true", help="print the calls instead of running them")
    args, ignored = parser.parse_known_args()  # shortcuts and launchers can pass extra arguments

    os.makedirs(DATA_DIR, exist_ok=True)
    # About 3 MB of log at most: grain.log plus two older files
    handlers = [logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    for handler in handlers:
        handler.setFormatter(PrivateFormatter("%(asctime)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if ignored:
        log.info("ignored arguments: %s", ignored)
    config = load_config()

    if not (args.wav or args.text) and not claim_single_instance():
        log.info("already running: opening its tool editor instead")
        webbrowser.open(editor_url(config))
        return

    if args.wav or args.text:
        grain = Grain(config, print)
        if args.wav:
            heard, results = grain.engine.hear(args.wav, execute=not args.dry_run).result()
            print(f"heard: {heard}")
        else:
            results = grain.engine.interpret(args.text, execute=not args.dry_run, source="cli").result()
        print("\n".join(r["message"] for r in results) or "(nothing to do)")
        return
    TrayApp(config).run()
