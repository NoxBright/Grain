"""End-to-end checks for every tool, the argument cleaning and the profiles.

Windows side effects (opening programs, key presses, locking, timers, the browser) are
intercepted, so running this changes nothing on screen:

    .venv\\Scripts\\python -m unittest discover tests -v
"""
import copy
import ctypes
import os
import sys
import tempfile
import threading
import unittest
import webbrowser
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("GRAIN_DATA_DIR", tempfile.mkdtemp(prefix="grain-tests-data-"))  # never the real %APPDATA%\Grain
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
import grain.offline  # noqa: E402,F401

import keyboard  # noqa: E402

import grain.builtin  # noqa: E402
import grain.config  # noqa: E402
import grain.launch  # noqa: E402
from grain import profiles  # noqa: E402
from grain.config import DEFAULT_CONFIG, Config  # noqa: E402
from grain.phrases import clean_arguments, parse_number  # noqa: E402
from grain.registry import Tool  # noqa: E402

REAL_START_MENU_APPS = grain.launch.start_menu_apps
# A made-up Start menu, including the kinds of entries that must never be opened by "open X"
FAKE_START_MENU = {"Steam": "Valve.Steam", "Steam Support Center": "http://support.steampowered.com/",
                   "GOG GALAXY": "GogCom.GalaxyClient.Main", "Discord": "com.squirrel.Discord.Discord",
                   "Uninstall Discord": "uninstall-discord", "Claude": "Claude_x!Claude", "ChatGPT": "OpenAI.ChatGPT!App",
                   "Visual Studio Code": "Microsoft.VisualStudioCode", "Notepad++": "notepad-plus-plus"}
TEMP = tempfile.mkdtemp(prefix="grain-tests-")
NOTES = os.path.join(TEMP, "notes.md")


class Effects:
    """Records what tools tried to do instead of letting them do it."""

    def __init__(self):
        self.opened, self.browsed, self.keys, self.typed, self.pressed, self.timers = [], [], [], [], [], []
        self.locked = 0

    def clear(self):
        self.__init__()


effects = Effects()


class FakeTimer:
    def __init__(self, seconds, function):
        self.seconds, self.function, self.daemon = seconds, function, False
        effects.timers.append(seconds)

    def start(self):
        pass


def setUpModule():
    global grain_app, patches
    patches = [
        mock.patch("grain.launch.os.startfile", lambda target: effects.opened.append(target)),
        mock.patch("grain.launch.start_menu_apps", lambda refresh=False: dict(FAKE_START_MENU)),
        mock.patch.object(webbrowser, "open", lambda url, *a, **k: effects.browsed.append(url)),
        mock.patch.object(keyboard, "send", lambda keys, *a, **k: effects.keys.append(keys)),
        mock.patch.object(keyboard, "write", lambda text, *a, **k: effects.typed.append(text)),
        mock.patch("grain.builtin.press_key", lambda vk, times=1: effects.pressed.append((vk, times))),
        mock.patch.object(ctypes.windll.user32, "LockWorkStation", lambda: setattr(effects, "locked", effects.locked + 1)),
        mock.patch.object(threading, "Timer", FakeTimer),
        mock.patch.object(grain.config, "NOTES_PATH", NOTES),
    ]
    for patch in patches:
        patch.start()
    from grain.app import Grain  # after patching: plugins read NOTES_PATH when they load
    config = Config(copy.deepcopy(DEFAULT_CONFIG))
    config["api_token"] = "test"
    grain.config._install_defaults(config)  # the default tools and plugins, into the temporary data folder
    grain_app = Grain(config, lambda message: None, history_path=os.path.join(TEMP, "history.jsonl"))
    assert not grain_app.load_errors, grain_app.load_errors
    assert grain_app.engine.ready.wait(60) and not grain_app.engine.load_error


def tearDownModule():
    for patch in patches:
        patch.stop()


def say(text, profile="default", execute=True):
    effects.clear()
    return grain_app.engine.interpret(text, execute=execute, profile=profile, source="test").result(timeout=120)


class PhraseCommands(unittest.TestCase):
    """Each tool through its spoken phrases, the way Whistle writes them (capitals, full stops)."""

    def ran(self, text, profile="default"):
        results = say(text, profile)
        self.assertEqual(len(results), 1, results)
        result = results[0]
        self.assertEqual(result["matched"], "phrase", f"{text!r} matched no phrase: {result}")
        self.assertEqual(result["outcome"], "executed", result)
        return result

    def test_open_windows_apps(self):
        self.ran("Open Notepad.")
        self.assertEqual(effects.opened, ["notepad.exe"])
        self.ran("Open the calculator.")
        self.assertEqual(effects.opened, ["calc.exe"])
        self.ran("Launch Settings.")
        self.assertEqual(effects.opened, ["ms-settings:"])

    def test_open_programs_and_nicknames(self):
        for spoken, start_name in [("Open Steam.", "Steam"), ("Open GOG.", "GOG GALAXY"), ("Open G.O.G.", "GOG GALAXY"),
                                   ("Launch GOG Galaxy.", "GOG GALAXY"), ("Open Discord.", "Discord"),
                                   ("Open Claude.", "Claude"), ("Open Chat GPT.", "ChatGPT"), ("Open chatgpt.", "ChatGPT"),
                                   ("Start VS Code.", "Visual Studio Code"), ("Open up Visual Studio Code.", "Visual Studio Code"),
                                   ("Open Visual Studio.", "Visual Studio Code"), ("Open Notepad plus plus.", None)]:
            with self.subTest(spoken):
                if start_name is None:  # spoken "plus plus" isn't a confident match for "Notepad++": open nothing
                    say(spoken)
                    self.assertEqual(effects.opened, [])
                    continue
                self.ran(spoken)
                self.assertEqual(effects.opened, [f"shell:AppsFolder\\{FAKE_START_MENU[start_name]}"])

    def test_program_names_are_matched_carefully(self):
        from grain.launch import find_start_app
        self.assertEqual(find_start_app("steam"), "Steam")  # not "Steam Support Center"
        self.assertEqual(find_start_app("discrod"), "Discord")  # close spelling
        self.assertIsNone(find_start_app("uninstall discord"))  # uninstallers are never programs
        self.assertIsNone(find_start_app("support center"))  # neither are website links
        self.assertIsNone(find_start_app("spotify"))  # not installed

    def test_catch_all_open_lets_other_requests_through(self):
        result = say("Start a timer for five minutes.")[0]
        self.assertNotIn("open_program", [c["name"] for c in result["calls"]], result)
        result = say("Open Spotify.")[0]  # not installed: no phrase claims it, nothing is opened
        self.assertNotEqual(result["matched"], "phrase")
        self.assertEqual(effects.opened, [])

    def test_phrases_work_in_every_profile(self):
        for profile in grain_app.config["profiles"]:
            with self.subTest(profile):
                self.ran("Open Steam.", profile)
                self.ran("Lock the computer.", profile)  # lock_computer is only in the system profile

    def test_web_search(self):
        self.ran("Search for hexapod gait patterns.")
        self.assertEqual(effects.browsed, ["https://duckduckgo.com/?q=hexapod+gait+patterns"])
        self.ran("Look up C++ templates")
        self.assertEqual(effects.browsed, ["https://duckduckgo.com/?q=C%2B%2B+templates"])

    def test_youtube(self):
        self.ran("YouTube lo-fi beats.")
        self.assertEqual(effects.opened, ["https://www.youtube.com/results?search_query=lo-fi+beats"])
        self.ran("Search YouTube for Magic the Gathering.")
        self.assertEqual(effects.opened, ["https://www.youtube.com/results?search_query=Magic+the+Gathering"])
        self.ran("Open YouTube.")
        self.assertEqual(effects.opened, ["https://www.youtube.com/"])

    def test_volume(self):
        up, down, mute = grain.builtin.VK["volume_up"], grain.builtin.VK["volume_down"], grain.builtin.VK["mute"]
        self.ran("Volume up.")
        self.assertEqual(effects.pressed, [(up, 5)])
        self.ran("Turn the volume down ten steps.")
        self.assertEqual(effects.pressed, [(down, 10)])
        self.ran("Louder.")
        self.assertEqual(effects.pressed, [(up, 5)])
        self.ran("Mute.")
        self.assertEqual(effects.pressed, [(mute, 1)])

    def test_media(self):
        self.ran("Next song.")
        self.assertEqual(effects.pressed, [(grain.builtin.VK["next"], 1)])
        self.ran("Pause.")
        self.assertEqual(effects.pressed, [(grain.builtin.VK["play_pause"], 1)])

    def test_time(self):
        self.assertTrue(self.ran("What time is it?")["message"].startswith("It's "))

    def test_lock(self):
        self.ran("Lock my computer.")
        self.assertEqual(effects.locked, 1)

    def test_screenshot(self):
        self.ran("Take a screenshot.")
        self.assertEqual(effects.keys, ["windows+shift+s"])
        keyboard.parse_hotkey("windows+shift+s")  # raises if the key names are wrong

    def test_battery(self):
        message = self.ran("Battery level.")["message"]
        self.assertTrue("Battery at" in message or "No battery" in message, message)

    def test_dice(self):
        message = self.ran("Roll two twenty sided dice.")["message"]
        rolls = [int(n) for n in message.split("[")[1].split("]")[0].split(",")]
        self.assertEqual(len(rolls), 2)
        self.assertTrue(all(1 <= r <= 20 for r in rolls), rolls)
        self.assertIn("rolled", self.ran("Roll dice.")["message"])

    def test_timers(self):
        for spoken, seconds in [("Set a timer for five minutes.", 300), ("Set a timer for 90 seconds.", 90),
                                ("Set a timer for twenty five seconds.", 25), ("Set a timer for an hour.", 3600),
                                ("Set a timer for one hour and thirty minutes.", 5400), ("Ten minute timer.", 600)]:
            with self.subTest(spoken):
                self.ran(spoken)
                self.assertEqual(effects.timers, [seconds])

    def test_notes(self):
        self.ran("Take a note to buy more solder.")
        with open(NOTES, encoding="utf-8") as handle:
            self.assertTrue(handle.read().rstrip().endswith("buy more solder"))

    def test_chained(self):
        results = say("Open notepad then next song")
        self.assertEqual([r["outcome"] for r in results], ["executed", "executed"])
        self.assertEqual(effects.opened, ["notepad.exe"])
        self.assertEqual(effects.pressed, [(grain.builtin.VK["next"], 1)])

    def test_preview_does_nothing(self):
        result = say("Open Steam.", execute=False)[0]
        self.assertEqual(result["outcome"], "dry_run")
        self.assertEqual(effects.opened, [])


class EdgeCases(unittest.TestCase):
    """Handlers called directly with the awkward values a model or a person might produce."""

    def handler(self, name):
        return grain_app.registry.get(name).handler

    def test_empty_inputs_are_refused_politely(self):
        effects.clear()
        self.assertEqual(self.handler("search_web")("   "), "What should I search for?")
        self.assertEqual(self.handler("take_note")("  "), "What should the note say?")
        self.assertEqual(self.handler("set_timer")(), "How long should the timer be?")
        self.assertEqual(effects.browsed + effects.timers, [])

    def test_timer_limits(self):
        effects.clear()
        self.assertIn("at most", self.handler("set_timer")(hours=25))
        self.assertEqual(self.handler("set_timer")(minutes=-5), "How long should the timer be?")
        self.assertEqual(effects.timers, [])

    def test_volume_steps_are_clamped(self):
        effects.clear()
        self.handler("adjust_volume")("up", 500)
        self.handler("adjust_volume")("down", 0)
        self.assertEqual([times for _, times in effects.pressed], [50, 5])


class ArgumentCleaning(unittest.TestCase):
    """What happens to Needle's arguments before a tool runs."""

    def clean(self, name, arguments):
        return clean_arguments(grain_app.registry.get(name), arguments)

    def test_types_are_converted(self):
        self.assertEqual(self.clean("adjust_volume", {"direction": "UP", "steps": "three"}), ({"direction": "up", "steps": 3}, None))
        self.assertEqual(self.clean("set_timer", {"minutes": "twenty five"}), ({"minutes": 25}, None))
        self.assertEqual(self.clean("roll_dice", {"count": 2.0, "sides": 20}), ({"count": 2, "sides": "20"}, None))

    def test_invented_and_empty_arguments_are_dropped(self):
        self.assertEqual(self.clean("tell_time", {"timezone": "UTC"}), ({}, None))
        self.assertEqual(self.clean("open_youtube", {"query": ""}), ({}, None))

    def test_bad_values_are_refused(self):
        arguments, problem = self.clean("adjust_volume", {"direction": "louder"})
        self.assertIsNone(arguments)
        self.assertIn("one of up, down, mute", problem)
        self.assertIn("needs direction", self.clean("adjust_volume", {})[1])
        self.assertIn("isn't a valid integer", self.clean("roll_dice", {"count": "lots"})[1])

    def test_unknown_tool_is_refused(self):
        calls, problem = grain_app.engine._clean([{"name": "format_disk", "arguments": {}}])
        self.assertEqual(calls, [])
        self.assertIn("no tool called format_disk", problem)

    def test_spoken_numbers(self):
        for spoken, value in [("3", 3), ("1,000", 1000), ("twenty five", 25), ("a hundred", 100), ("one hundred and five", 105),
                              ("one and a half", 1.5), ("half", 0.5), ("ninety-nine", 99), ("lots", None)]:
            with self.subTest(spoken):
                self.assertEqual(parse_number(spoken), value)


class Confirmation(unittest.TestCase):
    def setUp(self):
        self.calls = []
        tool = Tool("test_confirm_tool", "A test tool that must be confirmed.", {"type": "object", "properties": {}, "required": []},
                    lambda: self.calls.append(1) or "ran", source="remote", safety="confirm", origin="tests",
                    phrases=["run the confirm test"])
        grain_app.registry.replace("remote", [tool], origin="tests")

    def tearDown(self):
        grain_app.registry.replace("remote", [], origin="tests")

    def test_waits_then_runs_on_yes(self):
        self.assertEqual(say("Run the confirm test.")[0]["outcome"], "needs_confirm")
        self.assertEqual(self.calls, [])
        self.assertEqual(say("Yes.")[0]["outcome"], "confirmed")
        self.assertEqual(self.calls, [1])

    def test_cancel(self):
        say("Run the confirm test.")
        self.assertEqual(say("Cancel.")[0]["outcome"], "cancelled")
        self.assertEqual(self.calls, [])

    def test_yes_must_be_the_whole_reply(self):
        say("Run the confirm test.")
        for request in ["Okay, what time is it.", "Yeah no.", "Do it later.", "Ok play some music."]:
            self.assertNotEqual(say(request)[0]["outcome"], "confirmed", request)
        self.assertEqual(self.calls, [])
        self.assertEqual(say("Yes, please.")[0]["outcome"], "confirmed")
        self.assertEqual(self.calls, [1])

    def test_programs_cannot_say_yes(self):
        say("Run the confirm test.")
        results = grain_app.engine.interpret("yes", source="program:tests", may_confirm=False).result(timeout=120)
        self.assertNotEqual(results[0]["outcome"], "confirmed")
        self.assertEqual(self.calls, [])
        self.assertEqual(say("Cancel.")[0]["outcome"], "cancelled")

    def test_listing_while_calls_expire_on_other_threads(self):
        engine, entry = grain_app.engine, grain_app.history.add(text="expiry race")
        with engine._pending_lock:
            for i in range(300):
                engine.pending[f"race{i}"] = {"id": f"race{i}", "text": "", "calls": [], "summary": "", "expires": 0,
                                              "history_id": entry["id"], "source": "test"}
        failures = []

        def lister():
            try:
                for _ in range(50):
                    engine.pending_list()
            except Exception as error:  # a KeyError or "dictionary changed size" before the lock
                failures.append(error)

        threads = [threading.Thread(target=lister) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertFalse(any(i.startswith("race") for i in engine.pending))


class Profiles(unittest.TestCase):
    def test_every_profile_is_small_and_valid(self):
        names = {t.name for t in grain_app.registry.all()}
        for profile, patterns in grain_app.config["profiles"].items():
            with self.subTest(profile):
                selected = profiles.select_tools(grain_app.registry.all(), grain_app.config, profile)
                self.assertLessEqual(len(selected), profiles.TOO_MANY_TOOLS)
                for pattern in patterns:  # catches typos in tool names
                    self.assertTrue(any(__import__("fnmatch").fnmatchcase(n, pattern) for n in names), pattern)

    def test_every_tool_is_in_some_profile(self):
        covered = {t.name for p in grain_app.config["profiles"]
                   for t in profiles.select_tools(grain_app.registry.all(), grain_app.config, p)}
        self.assertEqual({t.name for t in grain_app.registry.all()} - covered, set())

    def test_app_in_front_picks_the_profile(self):
        for process, expected in [("Code.exe", "coding"), ("steamwebhelper.exe", "gaming"), ("GalaxyClient.exe", "gaming"),
                                  ("Spotify.exe", "media"), ("notepad.exe", "default"), ("", "default")]:
            with self.subTest(process), mock.patch.object(profiles, "foreground_process", lambda: process):
                self.assertEqual(profiles.current_profile(grain_app.config), expected)

    def test_fixed_profile_overrides_rules(self):
        config = Config(copy.deepcopy(dict(grain_app.config)), active_profile="system")
        with mock.patch.object(profiles, "foreground_process", lambda: "Code.exe"):
            self.assertEqual(profiles.current_profile(config), "system")


class ThisComputer(unittest.TestCase):
    """Looks things up on this PC without opening them."""

    def test_real_start_menu_reads_and_every_program_finds_itself(self):
        from grain import launch
        with mock.patch.object(launch, "start_menu_apps", REAL_START_MENU_APPS):
            apps = REAL_START_MENU_APPS(refresh=True)
            self.assertGreater(len(apps), 0)
            programs = [n for n, a in apps.items() if not launch.NOT_PROGRAMS.search(n) and not a.lower().startswith("http")]
            missed = [n for n in programs if launch.find_start_app(n) != n
                      and launch._simple(launch.find_start_app(n) or "") != launch._simple(n)]
            self.assertEqual(missed, [], "Start-menu programs that can't be opened by their own name")


class NeedleFallback(unittest.TestCase):
    def test_ungrounded_confident_guess_is_skipped(self):
        # Needle picks media_control at ~0.67 for this in the gaming profile; nothing about media was said.
        result = say("could you fire up steam for me", "gaming")[0]
        self.assertNotEqual(result["outcome"], "executed", result)
        self.assertEqual(effects.pressed + effects.opened, [])

    def test_grounding_words(self):
        from grain.engine import grounded
        media, battery = grain_app.registry.get("media_control"), grain_app.registry.get("battery_level")
        self.assertTrue(grounded(media, "skip this track please"))
        self.assertFalse(grounded(media, "could you fire up steam for me"))
        self.assertTrue(grounded(battery, "how much charge does my laptop have left"))

    def test_nonsense_runs_nothing(self):
        result = say("tell me a joke about penguins")[0]
        self.assertIn(result["outcome"], ("no_call", "low_confidence", "invalid"))
        self.assertEqual(effects.opened + effects.browsed + effects.keys, [])


if __name__ == "__main__":
    unittest.main()
