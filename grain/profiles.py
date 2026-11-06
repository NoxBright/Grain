"""Tool profiles: which tools Needle sees right now. Needle gets less accurate as the
tool list grows, so each profile is a short list of name patterns, picked by hand or by
the app in the foreground."""
import ctypes
import fnmatch
import os
from ctypes import wintypes

TOO_MANY_TOOLS = 10  # past this the base model's accuracy drops noticeably


def foreground_process():
    """The exe name of the window in front, e.g. 'Code.exe', or '' if it can't be read."""
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


def current_profile(config):
    chosen = config.get("active_profile", "auto")
    if chosen != "auto" and chosen in config["profiles"]:
        return chosen
    process = foreground_process().lower()
    for rule in config.get("profile_rules", []):
        if process and fnmatch.fnmatch(process, rule.get("process", "").lower()) and rule.get("profile") in config["profiles"]:
            return rule["profile"]
    return "default" if "default" in config["profiles"] else next(iter(config["profiles"]), "default")


def select_tools(tools, config, profile):
    patterns = config["profiles"].get(profile, ["*"])
    return [t for t in tools if any(fnmatch.fnmatchcase(t.name, p) for p in patterns)]
