"""Tool profiles: which tools Needle sees right now. Needle gets less accurate as the
tool list grows, so each profile is a short list of name patterns, picked by hand or by
the app in the foreground."""
import fnmatch

from . import platform

TOO_MANY_TOOLS = 10  # past this the base model's accuracy drops noticeably


def foreground_process():
    """The exe name of the window in front, e.g. 'Code.exe', or '' if it can't be read."""
    return platform.foreground_process()


def current_profile(config):
    chosen = config.get("active_profile", "auto")
    if chosen != "auto" and chosen in config["profiles"]:
        return chosen
    process = foreground_process().lower()
    for rule in config.get("profile_rules", []):
        pattern = rule.get("process") if isinstance(rule, dict) else None
        if process and isinstance(pattern, str) and fnmatch.fnmatch(process, pattern.lower()) \
                and rule.get("profile") in config["profiles"]:
            return rule["profile"]
    return "default" if "default" in config["profiles"] else next(iter(config["profiles"]), "default")


def select_tools(tools, config, profile):
    patterns = config["profiles"].get(profile, ["*"])
    return [t for t in tools if any(fnmatch.fnmatchcase(t.name, p) for p in patterns)]
