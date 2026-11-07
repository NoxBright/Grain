"""Open installed programs by their Start-menu name or a user-defined alias."""
import json

from grain import open_target, tool
from grain.config import CONFIG_PATH
from grain.launch import find_start_app

VERBS = ["open", "open up", "open the", "launch", "start", "start up", "run"]


def aliases():
    """Read app_aliases without importing or rewriting the live application config."""
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as handle:
            value = json.load(handle).get("app_aliases", {})
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def is_installed(arguments):
    """Only claim an open phrase when the requested program is installed."""
    return find_start_app(arguments.get("app", ""), aliases()) is not None


@tool(keywords=["program", "app", "launch"],
      phrases=[f"{verb} {{app}}" for verb in VERBS], check=is_installed)
def open_program(app: str):
    "Open any installed program by its Start-menu name or a user-defined alias."
    name = find_start_app(app, aliases())
    if name is None:
        return f"Couldn't find a program called '{app}' in the Start menu"
    open_target("start:" + name)
    return f"opened {name}"
