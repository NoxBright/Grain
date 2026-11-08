"""No-code tools: one JSON file per tool in tools\\, reloaded as soon as a file changes.

    {
      "name": "open_youtube",
      "description": "Open YouTube, searching for something if given.",
      "parameters": {"query": {"type": "string", "required": false}},
      "action": {"type": "open", "target": "https://www.youtube.com/results?search_query={query}"},
      "phrases": ["search youtube for {query}", "youtube {query}"],
      "safety": "run"
    }

Actions, with {param} placeholders filled from Needle's arguments:
    open        target: a URL, file, folder, program or start:<Start-menu name> (URL placeholders are
                URL-encoded); optional empty_target is opened instead when none of its details were said
    run         program and args: started directly, never through a shell
    keys        keys: a shortcut like "win+shift+s", and/or text to type
    powershell  script (run with pwsh off Windows): arguments arrive as GRAIN_<param> in the
                environment, never pasted into the script
    http        method, url, headers, body: a request to a local or web service
"""
import json
import logging
import os
import re
import string
import subprocess
import threading
import urllib.parse
import urllib.request

from . import platform
from .launch import open_target
from .registry import Tool

log = logging.getLogger("grain")

ACTIONS = ("open", "run", "keys", "powershell", "http")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def is_url(target):
    return re.match(r"^[a-z][a-z0-9+.-]*:", target) is not None and "://" in target


def default_safety(action):
    """Ask first for anything that could do anything, unless the tool file says otherwise:
    scripts and programs; opening a file or program whose path comes from what was said (a
    misheard word could open anything); and typing spoken text into whatever window is in front."""
    kind = action.get("type")
    if kind in ("run", "powershell"):
        return "confirm"
    if kind == "open" and not is_url(str(action.get("target", ""))) and PLACEHOLDER.search(str(action.get("target", ""))):
        return "confirm"
    if kind == "keys" and PLACEHOLDER.search(str(action.get("text", ""))):
        return "confirm"
    return "run"


def to_schema(parameters):
    """Accept either a full JSON schema or the short {name: {type, description, enum, required}} form."""
    if parameters.get("type") == "object":
        return parameters
    properties, required = {}, []
    for name, spec in parameters.items():
        spec = dict(spec) if isinstance(spec, dict) else {"type": str(spec)}
        if spec.pop("required", True):
            required.append(name)
        spec.setdefault("type", "string")
        properties[name] = spec
    return {"type": "object", "properties": properties, "required": required}


class _Blank(dict):
    def __missing__(self, key):  # optional parameters Needle left out
        return ""


def fill(template, arguments, quote=False):
    values = _Blank({k: urllib.parse.quote_plus(str(v)) if quote else str(v) for k, v in arguments.items()})
    return string.Formatter().vformat(template, (), values)


def fill_json(value, arguments):
    """Placeholders inside a JSON body; a value that is exactly "{x}" keeps x's type."""
    if isinstance(value, str):
        match = re.fullmatch(r"\{(\w+)\}", value)
        return arguments.get(match.group(1)) if match else fill(value, arguments)
    if isinstance(value, list):
        return [fill_json(v, arguments) for v in value]
    if isinstance(value, dict):
        return {k: fill_json(v, arguments) for k, v in value.items()}
    return value


def make_handler(action):
    kind = action.get("type")
    if kind not in ACTIONS:
        raise ValueError(f"action type must be one of {', '.join(ACTIONS)}")

    if kind == "open":
        target, empty_target = action["target"], action.get("empty_target")
        quote = is_url(target)
        used = PLACEHOLDER.findall(target)

        def handler(**arguments):
            if empty_target and not any(str(arguments.get(name, "")).strip() for name in used):
                filled = empty_target  # e.g. the home page when no search words were said
            else:
                filled = fill(target, arguments, quote=quote)
            open_target(filled)
            return f"opened {filled}"
    elif kind == "run":
        program, args = action["program"], action.get("args", [])

        def handler(**arguments):
            command = [fill(program, arguments)] + [fill(a, arguments) for a in args]
            subprocess.Popen(command, cwd=action.get("cwd") or None)
            return "started " + os.path.basename(command[0])
    elif kind == "keys":
        def handler(**arguments):
            import keyboard
            if action.get("keys"):
                keyboard.send(action["keys"])
            if action.get("text"):
                keyboard.write(fill(action["text"], arguments))
            return "sent " + (action.get("keys") or "text")
    elif kind == "powershell":
        script = action["script"]
        # Windows PowerShell takes -ExecutionPolicy; pwsh on Linux has no such concept and rejects it.
        flags = ["-NoProfile", "-ExecutionPolicy", "Bypass"] if platform.IS_WINDOWS else ["-NoProfile"]
        shell = "powershell" if platform.IS_WINDOWS else "pwsh"

        def handler(**arguments):
            env = dict(os.environ, **{f"GRAIN_{k}": str(v) for k, v in arguments.items()})
            done = subprocess.run([shell, *flags, "-Command", script],
                                  env=env, capture_output=True, text=True, timeout=action.get("timeout", 15),
                                  **platform.no_window())
            output = (done.stdout or done.stderr).strip()
            return output[:200] or f"script finished (exit {done.returncode})"
    else:  # http
        def handler(**arguments):
            body = action.get("body")
            data = json.dumps(fill_json(body, arguments)).encode() if body is not None else None
            request = urllib.request.Request(
                fill(action["url"], arguments, quote=True), data=data, method=action.get("method", "POST" if data else "GET"),
                headers={"Content-Type": "application/json", **action.get("headers", {})})
            with urllib.request.urlopen(request, timeout=action.get("timeout", 5)) as response:
                return response.read(300).decode("utf-8", "replace").strip() or f"HTTP {response.status}"
    return handler


def tool_from_data(data, origin=""):
    action = data.get("action") or {}
    return Tool(
        name=data.get("name", ""), description=data.get("description", ""),
        parameters=to_schema(data.get("parameters", {})), handler=make_handler(action), source="file",
        safety=data.get("safety") or default_safety(action),
        threshold=data.get("threshold"), origin=origin, keywords=data.get("keywords", []),
        phrases=data.get("phrases", []))


def load_folder(folder):
    """Every tool file in the folder; bad files are reported, not fatal."""
    tools, errors = [], []
    os.makedirs(folder, exist_ok=True)
    for filename in sorted(os.listdir(folder)):
        if not filename.endswith(".json"):
            continue
        path = os.path.join(folder, filename)
        try:
            with open(path, encoding="utf-8-sig") as handle:
                tools.append(tool_from_data(json.load(handle), origin=path))
        except Exception as error:
            errors.append(f"{filename}: {error}")
    return tools, errors


def save_tool_file(folder, data):
    """Validate and write one tool file, named after the tool. Returns its path."""
    tool_from_data(data)  # raises on anything invalid
    path = os.path.join(folder, data["name"] + ".json")
    os.makedirs(folder, exist_ok=True)
    temporary = path + ".tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass
    return path


class FolderWatcher(threading.Thread):
    """Calls on_change() whenever files matching the suffix are added, edited or removed."""

    def __init__(self, folder, suffix, on_change, interval=1.5):
        super().__init__(daemon=True)
        self.folder, self.suffix, self.on_change, self.interval = folder, suffix, on_change, interval
        self.stopped = threading.Event()

    def snapshot(self):
        try:
            return sorted((f, os.path.getmtime(os.path.join(self.folder, f)))
                          for f in os.listdir(self.folder) if f.endswith(self.suffix))
        except OSError:
            return []

    def run(self):
        seen = self.snapshot()
        while not self.stopped.wait(self.interval):
            now = self.snapshot()
            if now != seen:
                seen = now
                try:
                    self.on_change()
                except Exception:
                    log.exception("reloading %s failed", self.folder)
