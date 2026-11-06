"""The handful of things Grain needs from the operating system, in the two ways of doing them.

Windows answers with the Win32 API through ctypes, the registry and PowerShell; Linux answers
with XDG folders, xdg-open and the sound server. Everything else in Grain is platform-neutral,
so no other module asks which machine it is on: they call the function here and get the same
job done either way.

Where Linux has no exact equivalent the closest thing is used, and where it has nothing the
call raises or returns "" rather than guessing. The engine turns a failing tool into a message
the user sees, so a missing desktop feature reads as "that didn't work", never as a crash.
"""
import logging
import os
import string
import subprocess
import sys

log = logging.getLogger("grain")

IS_WINDOWS = os.name == "nt"

# builtin.py names media keys by their Windows virtual-key codes so the tools read the same on
# either platform. These are the codes it uses, translated here into what each OS understands.
_VOLUME_UP, _VOLUME_DOWN, _MUTE = 0xAF, 0xAE, 0xAD
_KEYSYMS = {0xB0: "XF86AudioNext", 0xB1: "XF86AudioPrev",
            0xB2: "XF86AudioStop", 0xB3: "XF86AudioPlay"}

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"  # Windows: where per-user startup lives
RUN_NAME = "Grain"
AUTOSTART_FILE = "grain.desktop"  # Linux: ~/.config/autostart/<this>
_SINGLE_INSTANCE = "Local\\GrainApp-SingleInstance"  # Windows mutex name; the lock file uses the tail
_instance_handle = None  # held for the life of the process


# ---------------------------------------------------------------- opening things

def open_path(path):
    """Open a file, folder, program or URL the way the desktop would. Start-menu entries have
    already been resolved to a shell: path (Windows) or a .desktop file (Linux) by start_menu_target."""
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606 - the whole point is handing it to the shell
        return
    if path.endswith(".desktop"):
        # gtk-launch takes the entry's id and looks it up in the XDG application folders, the
        # same places the menu itself looks; running the file directly would ask the file manager.
        desktop_id = os.path.basename(path)[:-len(".desktop")]
        subprocess.Popen(["gtk-launch", desktop_id])
        return
    subprocess.Popen(["xdg-open", path])


def short_path(path):
    """Windows' 8.3 short form of a path, used to keep the account name out of the log. Linux
    paths have no short form, so this is always empty there and the log keeps the full path."""
    if IS_WINDOWS:
        import ctypes
        if not path or not os.path.exists(path):
            return ""
        buffer = ctypes.create_unicode_buffer(1024)
        ctypes.windll.kernel32.GetShortPathNameW(path, buffer, 1024)
        return buffer.value
    return ""


def no_window():
    """Extra subprocess.run() arguments that stop a helper from flashing a console window.
    Windows has CREATE_NO_WINDOW; POSIX has no such flag, and passing a non-zero one is an error."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}


# ---------------------------------------------------------------- hotkeys

_key_names = None


def parse_hotkey(value):
    """Raise ValueError if Grain doesn't understand this hotkey.

    Windows asks keyboard, which reads the keymap from the OS. Linux keyboard shells out to
    dumpkeys, which needs a console descriptor no normal process has, so the same names are
    checked against keyboard's own static tables instead: the names it would accept if it
    could read the keymap."""
    if IS_WINDOWS:
        import keyboard
        keyboard.parse_hotkey(value)
        return
    unknown = [part for part in _split_hotkey(value) if part not in _key_name_set()]
    if unknown:
        raise ValueError(f"unknown key {unknown[0]!r}")


def _split_hotkey(value):
    """Each key of a hotkey, normalised the way keyboard normalises it. Commas separate
    sequences of hotkeys ("ctrl+alt+t, ctrl+alt+y"), which are validated one at a time."""
    steps = [step for step in value.split(",") if step.strip()]
    return [part.strip() for step in steps for part in step.split("+") if part.strip()]


def _key_name_set():
    """Every key name keyboard knows, without asking the kernel for the keymap."""
    global _key_names
    if _key_names is None:
        names = set(string.ascii_lowercase + string.digits)
        names.update(f"f{number}" for number in range(1, 25))
        try:
            from keyboard import all_modifiers
            from keyboard._canonical_names import canonical_names
            names.update(canonical_names)  # aliases and their targets, e.g. "escape" -> "esc"
            names.update(canonical_names.values())
            names.update(all_modifiers)
        except Exception:  # keyboard missing: fall back to letters, digits and function keys
            log.warning("keyboard's key name tables unavailable; hotkey names not checked", exc_info=True)
        _key_names = names
    return _key_names


# ---------------------------------------------------------------- one instance

def claim_single_instance(name=_SINGLE_INSTANCE):
    """True if this is the only Grain running for this user. The handle stays open for the life
    of the process: releasing it is what lets a second instance in."""
    global _instance_handle
    if IS_WINDOWS:
        import ctypes
        _instance_handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
        return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS
    import fcntl
    runtime = os.environ.get("XDG_RUNTIME_DIR") or tempfile_dir()
    path = os.path.join(runtime, name.replace("\\", "-").lower() + ".lock")
    handle = open(path, "a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)  # fails rather than blocks if held
    except OSError:
        handle.close()
        return False
    _instance_handle = handle  # noqa: F841 - the lock lives as long as this reference does
    return True


def tempfile_dir():
    import tempfile
    return tempfile.gettempdir()


# ---------------------------------------------------------------- the desktop

def foreground_process():
    """The exe name of the window in front, e.g. 'Code.exe', or '' if it can't be read. Profile
    rules match on this, so '' simply means no rule fires and the default profile is used."""
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return os.path.basename(buffer.value)
            return ""
        finally:
            kernel32.CloseHandle(handle)
    return _xdotool_process("getactivewindow")


def _xdotool_process(window_command):
    """The process name behind an xdotool window lookup, or '' when there is no window system to
    ask. Wayland compositors only expose this to XWayland clients, so it often answers nothing."""
    try:
        window = subprocess.run(["xdotool", window_command], capture_output=True, text=True, timeout=2)
        if window.returncode or not window.stdout.strip():
            return ""
        pid = subprocess.run(["xdotool", "getwindowpid", window.stdout.strip()],
                            capture_output=True, text=True, timeout=2)
        if pid.returncode or not pid.stdout.strip():
            return ""
        with open(f"/proc/{pid.stdout.strip()}/comm", encoding="utf-8") as handle:
            return handle.read().strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def lock_workstation():
    """Lock the session, as Windows' LockWorkStation does. loginctl asks the session manager,
    which is what the lock screen is bound to on both GNOME and KDE."""
    if IS_WINDOWS:
        import ctypes
        ctypes.windll.user32.LockWorkStation()
        return
    _run_first([["loginctl", "lock-session"], ["xdg-screensaver", "lock"]])


def press_key(vk, times=1):
    """Send a media key given as a Windows virtual-key code, so builtin.py stays platform-neutral.
    Volume goes through the sound server rather than a synthetic keypress: it works whichever
    window system is in front, and it reports failure instead of silently doing nothing."""
    if IS_WINDOWS:
        import ctypes
        for _ in range(times):
            ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
            ctypes.windll.user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
        return
    if vk in (_VOLUME_UP, _VOLUME_DOWN, _MUTE):
        _set_volume(vk, times)
        return
    keysym = _KEYSYMS.get(vk)
    if keysym is None:
        raise RuntimeError(f"no key {vk} on this platform")
    _run_first([["xdotool", "key", "--repeat", str(times), "--delay", "30", keysym]])


def _set_volume(vk, steps):
    """Raise, lower or toggle the default sink. wpctl is PipeWire, pactl PulseAudio, amixer ALSA:
    whichever the machine actually has, tried in that order."""
    if vk == _MUTE:
        commands = [["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"],
                    ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                    ["amixer", "-q", "set", "Master", "toggle"]]
    else:
        amount, sign = f"{2 * steps}%", "+" if vk == _VOLUME_UP else "-"
        commands = [["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", amount + sign],
                    ["pactl", "set-sink-volume", "@DEFAULT_SINK@", amount + sign],
                    ["amixer", "-q", "set", "Master", amount + sign]]
    _run_first(commands)


def _run_first(commands):
    """Run the first command that exists and succeeds; raise if none do, so the tool reports why."""
    missing, failed = [], []
    for command in commands:
        if not shutil_which(command[0]):
            missing.append(command[0])
            continue
        done = subprocess.run(command, capture_output=True, text=True)
        if done.returncode == 0:
            return
        failed.append((command[0], (done.stderr or done.stdout).strip()))
    if failed:
        raise RuntimeError(f"{failed[0][0]}: {failed[0][1] or 'failed'}")
    raise RuntimeError("none of " + ", ".join(missing) + " is installed")


def shutil_which(program):
    import shutil
    return shutil.which(program)


def battery_percent():
    """The battery charge as a whole percent, or None when this machine has no usable battery.
    Windows asks the kernel; Linux reads what the power supply class exposes."""
    if IS_WINDOWS:
        import ctypes

        class SystemPowerStatus(ctypes.Structure):
            _fields_ = [("ac", ctypes.c_byte), ("flag", ctypes.c_byte), ("percent", ctypes.c_byte),
                        ("status", ctypes.c_byte), ("life", ctypes.c_uint), ("full_life", ctypes.c_uint)]

        state = SystemPowerStatus()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(state)):
            return None
        if state.flag & 0x80 or state.percent == 0xFF:  # 0x80: no battery, 0xFF: unknown
            return None
        return int(state.percent)
    return _sysfs_battery_percent()


def _sysfs_battery_percent():
    root = "/sys/class/power_supply"
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return None
    for entry in entries:
        folder = os.path.join(root, entry)
        try:
            with open(os.path.join(folder, "type"), encoding="utf-8") as handle:
                if handle.read().strip() != "Battery":
                    continue
            with open(os.path.join(folder, "capacity"), encoding="utf-8") as handle:
                return int(handle.read().strip())
        except (OSError, ValueError):
            continue
    return None


# ---------------------------------------------------------------- installed programs

def scan_applications():
    """Every installed program as {name: openable target}: Start-menu entries on Windows, .desktop
    files on Linux. The name is what someone says; the target is what open_path() is given."""
    if IS_WINDOWS:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
            capture_output=True, text=True, timeout=30, **no_window())
        entries = json_loads(done.stdout or "[]")
        entries = [entries] if isinstance(entries, dict) else entries
        return {e["Name"]: e["AppID"] for e in entries if e.get("Name") and e.get("AppID")}
    return _xdg_applications()


def _xdg_applications():
    """Applications from the XDG data folders, first folder winning so a user's own entry
    overrides the system one of the same name. Flatpak's exports are added because they sit
    outside XDG_DATA_DIRS unless the session put them there."""
    found = {}
    for folder in _xdg_application_folders():
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for filename in names:
            if not filename.endswith(".desktop"):
                continue
            entry = _read_desktop_entry(os.path.join(folder, filename))
            if entry and entry.get("Name") and entry["Name"] not in found:
                found[entry["Name"]] = os.path.join(folder, filename)
    return found


def _xdg_application_folders():
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share" + os.pathsep + "/usr/share"
    folders = [os.path.join(data_home, "applications")]
    folders += [os.path.join(d, "applications") for d in data_dirs.split(os.pathsep) if d]
    folders += [os.path.join(os.path.expanduser("~"), ".local/share/flatpak/exports/share/applications"),
                "/var/lib/flatpak/exports/share/applications"]
    seen, ordered = set(), []
    for folder in folders:  # de-duplicate while keeping the order, which is the precedence
        if folder not in seen:
            seen.add(folder)
            ordered.append(folder)
    return ordered


def _read_desktop_entry(path):
    """The fields of one .desktop file, or None when it isn't an app worth listing. Only the
    [Desktop Entry] group counts: the groups after it are translations and menu actions."""
    fields, group = {}, None
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if line.startswith("["):
                    if group is not None:
                        break
                    group = line
                    continue
                if group == "[Desktop Entry]" and "=" in line and not line.startswith("#"):
                    key, _, value = line.partition("=")
                    fields.setdefault(key.strip(), value.strip())
    except OSError:
        return None
    if fields.get("Type") != "Application":
        return None
    if fields.get("Hidden", "").lower() == "true" or fields.get("NoDisplay", "").lower() == "true":
        return None
    return fields


def start_menu_target(target):
    """Turn a raw target from scan_applications() into something open_path() accepts."""
    return f"shell:AppsFolder\\{target}" if IS_WINDOWS else target


# ---------------------------------------------------------------- starting with the session

def autostart_command():
    """What a session-autostart entry runs: the packaged app, or the script under whatever
    Python is running Grain. Windows needs pythonw so no console appears at login."""
    from .config import APP_DIR, FROZEN
    if FROZEN:
        return f'"{sys.executable}"'
    if IS_WINDOWS:
        python = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    else:
        python = sys.executable
    return f'"{python}" "{os.path.join(APP_DIR, "grain_app.py")}"'


def autostart_enabled():
    if IS_WINDOWS:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, RUN_NAME)
                return True
        except OSError:
            return False
    return os.path.exists(_autostart_path())


def set_autostart(enabled):
    """A per-user startup entry (no admin rights needed); the uninstaller removes it."""
    if IS_WINDOWS:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        return
    path = _autostart_path()
    if enabled:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[Desktop Entry]\nType=Application\nName=Grain\n"
                         f"Exec={autostart_command()}\nNoDisplay=true\nX-GNOME-Autostart-enabled=true\n")
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _autostart_path():
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(config_home, "autostart", AUTOSTART_FILE)


def json_loads(text):
    import json
    return json.loads(text)
