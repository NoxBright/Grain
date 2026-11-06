"""The built-in tools: general system commands only. Everything else lives in plugins\\ or tools\\."""
import datetime

from . import platform
from .launch import open_target
from .registry import Tool

VK = {"volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD,
      "next": 0xB0, "previous": 0xB1, "stop": 0xB2, "play_pause": 0xB3}


def press_key(vk, times=1):
    """Send one media key. Kept as a name of its own so tests can intercept every press."""
    platform.press_key(vk, times)


def builtin_tools(config, notify):
    aliases = config.get("app_aliases", {})

    def open_app(app):
        app = aliases.get(app.lower(), app)
        target = config["apps"].get(app) or config["apps"].get(app.lower())
        if target is None:
            return f"no app called {app!r} in the config"
        open_target(target)
        return f"opened {app}"

    def adjust_volume(direction, steps=5):
        steps = max(1, min(int(steps or 5), 50))  # each step is 2%, so at most the full range
        if direction == "mute":
            press_key(VK["mute"])
            return "toggled mute"
        press_key(VK["volume_" + direction], steps)
        return f"volume {direction} {steps * 2}%"

    def media_control(action):
        press_key(VK[action])
        return action.replace("_", "/")

    def tell_time():
        return datetime.datetime.now().strftime("It's %I:%M %p, %A %d %B")

    def lock_computer():
        platform.lock_workstation()
        return "locked"

    def battery_level():
        percent = platform.battery_percent()
        return "No battery found" if percent is None else f"Battery at {percent}%"

    def obj(properties, required=()):
        return {"type": "object", "properties": properties, "required": list(required)}

    def say(phrase, **arguments):
        return {"say": phrase, "arguments": arguments}

    verbs = ["open", "open up", "open the", "launch", "start", "run"]
    alias_phrases = [say(f"{verb} {alias}", app=app) for alias, app in aliases.items()
                     if app in config["apps"] for verb in verbs]

    return [
        Tool("open_app", "Open a Windows app such as Notepad, Calculator, File Explorer or Settings.",
             obj({"app": {"type": "string", "enum": list(config["apps"])}}, ["app"]), open_app,
             phrases=[f"{verb} {{app}}" for verb in verbs] + alias_phrases,
             keywords=list(aliases)),
        Tool("adjust_volume", "Turn the system volume up or down, or mute it.",
             obj({"direction": {"type": "string", "enum": ["up", "down", "mute"]},
                  "steps": {"type": "integer", "description": "Each step is 2 percent."}}, ["direction"]),
             adjust_volume, keywords=["volume"],
             phrases=["volume {direction}", "turn the volume {direction}", "turn it {direction}",
                      "turn the volume {direction} {steps} steps", "volume {direction} {steps} steps",
                      say("louder", direction="up"), say("quieter", direction="down"), say("softer", direction="down"),
                      say("mute", direction="mute"), say("unmute", direction="mute")]),
        Tool("media_control", "Play, pause, stop or skip to the next or previous song, track or video.",
             obj({"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]}}, ["action"]),
             media_control,
             phrases=[say("pause", action="play_pause"), say("play", action="play_pause"),
                      say("pause the music", action="play_pause"), say("resume", action="play_pause"),
                      say("next song", action="next"), say("skip", action="next"), say("skip song", action="next"),
                      say("next track", action="next"), say("previous song", action="previous"),
                      say("previous track", action="previous"), say("stop the music", action="stop")]),
        Tool("tell_time", "Show the current time and date.", obj({}), tell_time,
             phrases=["what time is it", "what's the time", "time", "what's the date", "what day is it"]),
        Tool("lock_computer", "Lock the computer screen.", obj({}), lock_computer, threshold=0.7,
             phrases=["lock the computer", "lock my computer", "lock computer", "lock the screen"]),
        Tool("battery_level", "Report the battery charge of this computer.", obj({}), battery_level,
             phrases=["battery", "battery level", "what's my battery", "what is my battery level"],
             keywords=["battery", "charge", "power", "laptop"]),
    ]
