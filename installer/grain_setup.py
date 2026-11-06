"""Grain's installer and uninstaller (one script, built twice by build.ps1).

    Grain Setup <v>.exe   carries the app folder as grain_payload.zip and installs or updates it
    Uninstall Grain.exe   the same code without a payload; removes Grain

Per-user: installs to %LOCALAPPDATA%\\Programs\\Grain, writes only to HKEY_CURRENT_USER, never asks
for admin rights. User data (%APPDATA%\\Grain) is kept on update and, unless asked, on uninstall.

Silent use:  --silent [--dir PATH] [--no-shortcuts] [--desktop] [--startup] [--no-launch] [--no-registry]
             --uninstall --silent [--remove-data] [--dir PATH]
"""
import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import winreg
import zipfile

APP = "Grain"
EXE = "Grain App.exe"
UNINSTALLER = "Uninstall Grain.exe"
BUNDLE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
PAYLOAD = os.path.join(BUNDLE, "grain_payload.zip")
DEFAULT_DIR = os.path.join(os.environ["LOCALAPPDATA"], "Programs", APP)
DATA_DIR = os.path.join(os.environ["APPDATA"], APP)
CACHE_DIR = os.path.join(os.environ["LOCALAPPDATA"], APP)
START_MENU = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs", f"{APP}.lnk")
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP}"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MUTEX = "Local\\GrainApp-SingleInstance"


def version():
    try:
        with open(os.path.join(BUNDLE, "version.txt"), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def desktop_shortcut():
    buffer = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer)  # CSIDL_DESKTOPDIRECTORY
    return os.path.join(buffer.value, f"{APP}.lnk")


# ---------------------------------------------------------------- pieces

def grain_running():
    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, MUTEX)  # SYNCHRONIZE
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    return False


def stop_grain():
    subprocess.run(["taskkill", "/F", "/T", "/IM", EXE], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    for _ in range(50):
        if not grain_running():
            return True
        time.sleep(0.1)
    return False


def make_shortcut(path, target, folder):
    """A .lnk via Windows Script Host; the paths travel in environment variables, never in the script."""
    script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath = $env:TARGET; "
              "$s.WorkingDirectory = $env:FOLDER; $s.IconLocation = $env:TARGET; $s.Description = 'Grain voice assistant'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, capture_output=True,
                   env=dict(os.environ, LNK=path, TARGET=target, FOLDER=folder), creationflags=subprocess.CREATE_NO_WINDOW)


def folder_size_kb(folder):
    return sum(os.path.getsize(os.path.join(root, f)) for root, _, files in os.walk(folder) for f in files) // 1024


def register(folder):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        uninstaller = os.path.join(folder, UNINSTALLER)
        for name, value in [("DisplayName", APP), ("DisplayVersion", version()), ("Publisher", APP),
                            ("InstallLocation", folder), ("DisplayIcon", os.path.join(folder, EXE)),
                            ("UninstallString", f'"{uninstaller}"'), ("QuietUninstallString", f'"{uninstaller}" --silent')]:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, folder_size_kb(folder))
        for name in ("NoModify", "NoRepair"):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1)


def installed_folder():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            return winreg.QueryValueEx(key, "InstallLocation")[0]
    except OSError:
        return None


def set_startup(folder, enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, f'"{os.path.join(folder, EXE)}"')
        else:
            try:
                winreg.DeleteValue(key, APP)
            except FileNotFoundError:
                pass


def remove_key(path):
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def same_path(a, b):
    """Windows may write one path in 8.3 short form (C:\\Users\\ABCDEF~1) and the other in full."""
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def shortcut_target(path):
    script = "(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK).TargetPath"
    found = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                           text=True, env=dict(os.environ, LNK=path), creationflags=subprocess.CREATE_NO_WINDOW)
    return found.stdout.strip()


def remove_shortcuts(folder):
    """Remove Grain's Start-menu and desktop shortcuts, but only ones that point at this Grain."""
    for shortcut in (START_MENU, desktop_shortcut()):
        if os.path.exists(shortcut) and same_path(shortcut_target(shortcut) or shortcut, os.path.join(folder, EXE)):
            os.remove(shortcut)


# Grain's own leftovers outside the install folder. User data (DATA_DIR) is only removed on request.
LEFTOVER_FOLDERS = [CACHE_DIR, os.path.join(os.environ["LOCALAPPDATA"], "NeedleAssistant")]  # audio library copies
SETUP_LOG = os.path.join(tempfile.gettempdir(), "grain-setup.log")
# Windows keeps a note of every program that has run; these are cleaned for Grain's own exes.
WINDOWS_RECORDS = [r"Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\MuiCache",
                   r"Software\Microsoft\Windows\CurrentVersion\Explorer\FeatureUsage\AppSwitched",
                   r"Software\Microsoft\Windows\CurrentVersion\Explorer\FeatureUsage\ShowJumpView",
                   r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Compatibility Assistant\Store"]


def grain_unpack_folders():
    """%TEMP%\\_MEI* folders left by Grain's single-file exes (the installer and uninstaller) if they were
    killed. Recognised by Grain's own files inside, so other programs' folders are never touched."""
    temp, mine = tempfile.gettempdir(), getattr(sys, "_MEIPASS", None)
    for name in os.listdir(temp):
        path = os.path.join(temp, name)
        is_unpack = name.startswith("_MEI") and all(c in "0123456789abcdefABCDEF" for c in name[4:]) and len(name) > 4
        if not (is_unpack and os.path.isdir(path)) or (mine and same_path(path, mine)):  # names are hex: _MEI00008a302
            continue
        if time.time() - os.path.getmtime(path) < 600:  # possibly a Grain installer running right now
            continue
        if any(os.path.exists(os.path.join(path, marker)) for marker in
               ("grain_payload.zip", os.path.join("grain", "ui", "index.html"), os.path.join("needle", "_worker.py"))) \
                or (os.path.exists(os.path.join(path, "grain.png")) and os.path.exists(os.path.join(path, "version.txt"))):
            yield path


def forget_windows_records(folder):
    """Remove Windows' per-program notes about Grain's exes (display names, app usage, compatibility)."""
    def ours(name):
        lowered = os.path.normcase(name.split(".FriendlyAppName")[0].split(".ApplicationCompany")[0])
        base = os.path.basename(lowered)
        return lowered.startswith(os.path.normcase(os.path.realpath(folder)) + "\\") or \
            lowered.startswith(os.path.normcase(folder) + "\\") or base in ("uninstall grain.exe", "grain app.exe") or \
            (base.startswith("grain setup ") and base.endswith(".exe"))
    for path in WINDOWS_RECORDS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                names = [winreg.EnumValue(key, i)[0] for i in range(winreg.QueryInfoKey(key)[1])]
                for name in names:
                    if ours(name):
                        winreg.DeleteValue(key, name)
        except OSError:
            pass


def remove_leftovers(folder):
    for path in LEFTOVER_FOLDERS + [folder + ".new", folder + ".old"] + list(grain_unpack_folders()):
        shutil.rmtree(path, ignore_errors=True)  # a folder still in use is skipped, never an error
    try:
        os.remove(SETUP_LOG)
    except OSError:
        pass
    forget_windows_records(folder)


# ---------------------------------------------------------------- install / uninstall

def is_grain_folder(folder):
    return os.path.isfile(os.path.join(folder, EXE))


def install(folder, shortcuts=True, desktop=False, startup=False, registry=True, progress=print):
    if os.path.isdir(folder) and os.listdir(folder) and not is_grain_folder(folder):
        raise RuntimeError(f"{folder} already has other files in it. Choose an empty folder.")
    if grain_running():
        progress("Closing Grain…")
        if not stop_grain():
            raise RuntimeError("Grain is still running. Quit it from the tray icon and try again.")
    progress("Copying files…")
    staging = folder + ".new"
    shutil.rmtree(staging, ignore_errors=True)
    with zipfile.ZipFile(PAYLOAD) as archive:
        archive.extractall(staging)
    if os.path.exists(folder):
        old = folder + ".old"
        shutil.rmtree(old, ignore_errors=True)
        os.replace(folder, old)  # fails cleanly if a file is still in use, leaving the old install intact
        os.replace(staging, folder)
        shutil.rmtree(old, ignore_errors=True)
    else:
        os.makedirs(os.path.dirname(folder), exist_ok=True)
        os.replace(staging, folder)
    target = os.path.join(folder, EXE)
    if shortcuts:
        progress("Adding to the Start menu…")
        make_shortcut(START_MENU, target, folder)
    if desktop:
        make_shortcut(desktop_shortcut(), target, folder)
    if registry:
        register(folder)
        set_startup(folder, startup)
    progress("Done.")
    return target


def uninstall(folder, remove_data=False, progress=print, schedule_self_delete=True):
    if not is_grain_folder(folder):  # never empty a folder that isn't a Grain install
        raise RuntimeError(f"{folder} doesn't look like a Grain install, so nothing was removed.")
    if grain_running():
        progress("Closing Grain…")
        stop_grain()
    remove_shortcuts(folder)
    set_startup(folder, False)
    remove_key(UNINSTALL_KEY)
    remove_leftovers(folder)
    if remove_data:
        shutil.rmtree(DATA_DIR, ignore_errors=True)

    me = sys.executable
    for entry in os.listdir(folder) if os.path.isdir(folder) else []:
        path = os.path.join(folder, entry)
        if same_path(path, me):
            continue
        shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else os.remove(path)
    if schedule_self_delete and same_path(os.path.dirname(me), folder):
        # A running program can't delete itself: a hidden command removes the folder once we've exited.
        subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 5 > nul & rmdir /s /q "{os.path.realpath(folder)}"',
                         cwd=tempfile.gettempdir(), close_fds=True,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    elif os.path.isdir(folder) and not os.listdir(folder):
        os.rmdir(folder)
    progress("Grain has been removed.")


# ---------------------------------------------------------------- window

def window(uninstalling, folder):
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title(f"Uninstall {APP}" if uninstalling else f"{APP} {version()} Setup")
    root.resizable(False, False)
    try:
        root.iconphoto(True, tk.PhotoImage(file=os.path.join(BUNDLE, "grain.png")).zoom(4))
    except tk.TclError:
        pass
    frame = ttk.Frame(root, padding=22)
    frame.grid()
    updating = not uninstalling and os.path.exists(os.path.join(folder, EXE))
    title = (f"Remove {APP} from this PC?" if uninstalling else
             f"Update {APP} to {version()}?" if updating else f"Install {APP} {version()}")
    ttk.Label(frame, text=title, font=("Segoe UI", 14, "bold")).grid(sticky="w")
    body = ("This removes the app, its shortcuts and its startup entry." if uninstalling else
            "Your settings, tools and history are kept." if updating else
            "An offline voice assistant. Speech recognition and the AI run on this PC, and nothing you say leaves it.\n"
            "Installs for you only, no administrator rights needed.")
    ttk.Label(frame, text=body, wraplength=430, justify="left").grid(sticky="w", pady=(6, 12))
    options = {}

    def option(key, text, default):
        options[key] = tk.BooleanVar(value=default)
        ttk.Checkbutton(frame, text=text, variable=options[key]).grid(sticky="w", pady=2)

    if uninstalling:
        option("remove_data", "Also delete my settings, history, notes and tools", False)
    else:
        option("shortcuts", "Add Grain to the Start menu", True)
        option("desktop", "Add a desktop shortcut", False)
        option("startup", "Start Grain when I sign in", False)
        option("launch", "Start Grain when setup finishes", True)
    status = ttk.Label(frame, text="", foreground="#666")
    status.grid(sticky="w", pady=(12, 0))
    buttons = ttk.Frame(frame)
    buttons.grid(sticky="e", pady=(14, 0))
    go = ttk.Button(buttons, text="Uninstall" if uninstalling else "Update" if updating else "Install")
    cancel = ttk.Button(buttons, text="Cancel", command=root.destroy)
    go.grid(row=0, column=0, padx=(0, 8))
    cancel.grid(row=0, column=1)

    def say(text):
        root.after(0, lambda: status.config(text=text))

    def work():
        try:
            if uninstalling:
                uninstall(folder, options["remove_data"].get(), progress=say)
                root.after(0, lambda: (messagebox.showinfo(APP, "Grain has been removed."), root.destroy()))
            else:
                target = install(folder, options["shortcuts"].get(), options["desktop"].get(), options["startup"].get(),
                                 progress=say)
                if options["launch"].get():
                    subprocess.Popen([target], cwd=folder, close_fds=True)
                root.after(0, lambda: (messagebox.showinfo(APP, "Grain is installed. Look for its icon by the clock."),
                                       root.destroy()))
        except Exception as error:
            root.after(0, lambda: (messagebox.showerror(APP, f"Something went wrong:\n{error}"),
                                   go.config(state="normal"), cancel.config(state="normal")))

    def start():
        if grain_running() and not messagebox.askokcancel(APP, "Grain is running. Close it and continue?"):
            return
        go.config(state="disabled")
        cancel.config(state="disabled")
        threading.Thread(target=work, daemon=True).start()

    go.config(command=start)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--dir")
    parser.add_argument("--no-shortcuts", action="store_true")
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--startup", action="store_true")
    parser.add_argument("--no-launch", action="store_true")
    parser.add_argument("--no-registry", action="store_true")
    parser.add_argument("--remove-data", action="store_true")
    args, _ = parser.parse_known_args()
    uninstalling = args.uninstall or not os.path.exists(PAYLOAD)  # without a payload this is the uninstaller
    here = os.path.dirname(os.path.abspath(sys.executable))
    folder = os.path.abspath(args.dir or (here if uninstalling and getattr(sys, "frozen", False) else
                                          installed_folder() or DEFAULT_DIR))
    if not args.silent:
        return window(uninstalling, folder)
    # Silent: never show a dialog. Problems go to %TEMP%\grain-setup.log and the exit code (1).
    log_path = os.path.join(tempfile.gettempdir(), "grain-setup.log")
    try:
        if uninstalling:
            uninstall(folder, args.remove_data, progress=lambda message: None)
        else:
            target = install(folder, not args.no_shortcuts, args.desktop, args.startup, registry=not args.no_registry,
                             progress=lambda message: None)
            if not args.no_launch:
                subprocess.Popen([target], cwd=folder, close_fds=True)
    except Exception as error:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {'uninstall' if uninstalling else 'install'} failed: {error}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
