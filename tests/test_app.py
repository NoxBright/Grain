"""The app around the tools: data folder, upgrades, settings, the settings API, one instance, updates."""
import copy
import hashlib
import http.client
import http.server
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _isolation  # noqa: E402,F401  (every temp folder is removed when the run ends)
os.environ.setdefault("GRAIN_DATA_DIR", tempfile.mkdtemp(prefix="grain-tests-data-"))
import grain.offline  # noqa: E402,F401

import grain.config as config_module  # noqa: E402
import grain.platform as platform  # noqa: E402
from grain import __version__, settings  # noqa: E402
from grain.config import DEFAULT_CONFIG, Config  # noqa: E402


def write(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DataFolder(unittest.TestCase):
    def setUp(self):
        self.app_dir, self.data_dir = tempfile.mkdtemp(), tempfile.mkdtemp()
        for folder in ("tools", "plugins"):
            os.makedirs(os.path.join(self.app_dir, "defaults", folder))
        write(os.path.join(self.app_dir, "defaults", "tools", "a.json"), "{}")
        write(os.path.join(self.app_dir, "defaults", "plugins", "b.py"), "")
        self.patches = [mock.patch.object(config_module, "DEFAULTS_DIR", os.path.join(self.app_dir, "defaults")),
                        mock.patch.object(config_module, "DATA_DIR", self.data_dir)]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        shutil.rmtree(self.app_dir, ignore_errors=True)
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def test_defaults_are_copied_once_and_deleted_ones_stay_deleted(self):
        config = {"installed_defaults": []}
        config_module._install_defaults(config)
        tool = os.path.join(self.data_dir, "tools", "a.json")
        self.assertTrue(os.path.exists(tool) and os.path.exists(os.path.join(self.data_dir, "plugins", "b.py")))
        os.remove(tool)  # the user deletes a default tool
        config_module._install_defaults(config)
        self.assertFalse(os.path.exists(tool))
        write(os.path.join(self.app_dir, "defaults", "tools", "new.json"), "{}")  # a later version adds one
        config_module._install_defaults(config)
        self.assertTrue(os.path.exists(os.path.join(self.data_dir, "tools", "new.json")))

    def test_user_edits_to_defaults_are_never_overwritten(self):
        config = {"installed_defaults": []}
        config_module._install_defaults(config)
        mine = os.path.join(self.data_dir, "tools", "a.json")
        write(mine, '{"edited": true}')
        config_module._install_defaults(config)
        self.assertEqual(read(mine), '{"edited": true}')


class Upgrades(unittest.TestCase):
    def test_settings_from_version_1_are_upgraded(self):
        old = {"hotkey": "ctrl+shift+g", "profiles": {"default": ["*"]}}  # no config_version: Grain 0.x
        merged = Config(copy.deepcopy(DEFAULT_CONFIG))
        merged.update(dict(old, config_version=1))
        upgraded = config_module.migrate(merged)
        self.assertEqual(upgraded["config_version"], config_module.CONFIG_VERSION)
        self.assertTrue(upgraded["setup_done"])  # upgraders don't get the first-run setup
        self.assertEqual(upgraded["hotkey"], "ctrl+shift+g")  # their choices survive

    def test_new_settings_need_setup(self):
        fresh = config_module.migrate(Config(copy.deepcopy(DEFAULT_CONFIG)))
        self.assertFalse(fresh["setup_done"])

    def test_legacy_files_beside_the_exe_move_to_the_data_folder(self):
        app_dir, data_dir = tempfile.mkdtemp(), tempfile.mkdtemp()
        write(os.path.join(app_dir, "assistant_config.json"), json.dumps({"hotkey": "ctrl+alt+g"}))
        write(os.path.join(app_dir, "history.jsonl"), '{"id": "x"}\n')
        targets = {"assistant_config.json": os.path.join(data_dir, "settings.json"),
                   "history.jsonl": os.path.join(data_dir, "history.jsonl"), "notes.md": os.path.join(data_dir, "notes.md")}
        with mock.patch.object(config_module, "APP_DIR", app_dir), mock.patch.object(config_module, "LEGACY_FILES", targets), \
                mock.patch.object(config_module, "CONFIG_PATH", targets["assistant_config.json"]):
            self.assertTrue(config_module._migrate_legacy_folder())
            self.assertFalse(config_module._migrate_legacy_folder())  # only once
        self.assertEqual(json.loads(read(targets["assistant_config.json"]))["hotkey"], "ctrl+alt+g")

    def test_settings_are_saved_atomically(self):
        path = os.path.join(tempfile.mkdtemp(), "settings.json")
        config = Config({"a": 1})
        config.path = path
        config.save()
        self.assertEqual(json.loads(read(path)), {"a": 1})
        self.assertFalse(os.path.exists(path + ".tmp"))


class SettingsValidation(unittest.TestCase):
    def test_good_values(self):
        clean, problems = settings.validate({"hotkey": "Ctrl+Alt+G", "confidence_threshold": 0.6, "wake_words": [" Hey  Grain "],
                                             "input_device": "", "max_seconds": 20.0})
        self.assertEqual(problems, {})
        self.assertEqual(clean, {"hotkey": "ctrl+alt+g", "confidence_threshold": 0.6, "wake_words": ["hey grain"],
                                 "input_device": None, "max_seconds": 20})

    def test_bad_values(self):
        _, problems = settings.validate({"hotkey": "g", "confidence_threshold": 2, "wake_words": [], "api_port": 80,
                                         "needle_weights": "model.bin", "update_url": "ftp://x", "api_token": "mine",
                                         "wake_word_mode": "yes"})
        self.assertEqual(set(problems), {"hotkey", "confidence_threshold", "wake_words", "api_port", "needle_weights",
                                         "update_url", "api_token", "wake_word_mode"})
        self.assertIn("isn't a setting", problems["api_token"])  # the key can't be set to a chosen value


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from grain.api import serve
        from grain.app import Grain
        config = Config(copy.deepcopy(DEFAULT_CONFIG))
        config["api_token"] = "test-token"
        config.path = os.path.join(tempfile.mkdtemp(), "settings.json")
        cls.grain = Grain(config, lambda m: None, history_path=os.path.join(tempfile.mkdtemp(), "h.jsonl"))
        cls.grain.mic_names = ["Test Mic"]
        cls.port = free_port()
        cls.server = serve(cls.grain, cls.port)
        cls.startup = mock.patch.object(settings, "set_start_with_windows")  # never touch the real registry
        cls.startup_mock = cls.startup.start()

    @classmethod
    def tearDownClass(cls):
        cls.startup.stop()
        cls.server.shutdown()

    def call(self, method, path, body=None, token="test-token"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        conn.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = conn.getresponse()
        try:
            return response.status, json.loads(response.read() or b"{}")
        finally:
            conn.close()

    def test_read_and_change_settings_live(self):
        status, data = self.call("GET", "/v1/settings")
        self.assertEqual(status, 200)
        self.assertEqual(data["microphones"], ["Test Mic"])
        self.assertEqual(data["version"], __version__)
        seen = []
        self.grain.on_settings_changed = seen.append
        status, data = self.call("PUT", "/v1/settings", {"hotkey": "ctrl+alt+g", "confidence_threshold": 0.7,
                                                          "start_with_windows": True})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.grain.config["hotkey"], "ctrl+alt+g")
        self.assertEqual(seen[-1], {"hotkey": "ctrl+alt+g", "confidence_threshold": 0.7})  # the tray re-registers the hotkey
        self.startup_mock.assert_called_with(True)
        self.assertTrue(os.path.exists(self.grain.config.path))

    def test_port_change_says_restart(self):
        status, data = self.call("PUT", "/v1/settings", {"api_port": 8799})
        self.assertEqual(data["restart_needed"], ["api_port"])

    def test_invalid_settings_change_nothing(self):
        before = dict(self.grain.config)
        status, data = self.call("PUT", "/v1/settings", {"hotkey": "q", "confidence_threshold": 0.8})
        self.assertEqual(status, 400)
        self.assertIn("hotkey", data["error"]["message"])
        self.assertEqual(self.grain.config["confidence_threshold"], before["confidence_threshold"])

    def test_setup_done_and_new_key(self):
        self.assertFalse(self.call("GET", "/v1/status")[1]["setup_done"])
        self.call("POST", "/v1/setup/done")
        self.assertTrue(self.call("GET", "/v1/status")[1]["setup_done"])
        status, data = self.call("POST", "/v1/settings/regenerate_token")
        new = data["api_token"]
        self.assertNotEqual(new, "test-token")
        self.assertEqual(self.call("GET", "/v1/status")[0], 401)  # the old key stops working at once
        self.assertEqual(self.call("GET", "/v1/status", token=new)[0], 200)
        self.grain.config["api_token"] = "test-token"

    def test_unknown_folder_refused(self):
        self.assertEqual(self.call("POST", "/v1/open/windows")[0], 404)

    def test_invalid_profile_rule_process_is_refused(self):
        profiles = self.call("GET", "/v1/profiles")[1]
        profiles["profile_rules"] = [{"process": None, "profile": profiles["profiles"].keys().__iter__().__next__()}]
        self.assertEqual(self.call("PUT", "/v1/profiles", profiles)[0], 400)

    # ------------------------------------------------------------ program keys

    def make_key(self, client):
        status, data = self.call("POST", "/v1/keys", {"client": client})
        self.assertEqual(status, 200, data)
        self.addCleanup(lambda: self.call("DELETE", f"/v1/keys/{data['id']}"))
        return data

    def raw(self, method, path, data, headers):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(method, path, data, {"Authorization": "Bearer test-token", **headers})
        response = conn.getresponse()
        try:
            return response.status, json.loads(response.read() or b"{}")
        finally:
            conn.close()

    def test_program_key_is_shown_once_and_stored_as_a_fingerprint(self):
        made = self.make_key("key_test_robot")
        self.assertRegex(made["key"], r"^grk_[0-9a-f]{8}_")
        listed = self.call("GET", "/v1/keys")[1]["keys"]
        entry = next(k for k in listed if k["client"] == "key_test_robot")
        self.assertNotIn("key", entry)
        self.assertNotIn("hash", entry)
        saved = read(self.grain.config.path)
        self.assertNotIn(made["key"], saved)
        self.assertIn(hashlib.sha256(made["key"].encode()).hexdigest(), saved)
        self.assertEqual(self.call("POST", "/v1/keys", {"client": "key_test_robot"})[0], 400)  # one key per program
        self.assertEqual(self.call("POST", "/v1/keys", {"client": "Not A Name"})[0], 400)

    def test_program_key_reaches_only_its_own_routes(self):
        key = self.make_key("scope_robot")["key"]
        status, data = self.call("GET", "/v1/status", token=key)
        self.assertEqual(status, 200)
        self.assertEqual(data["client"], "scope_robot")
        self.assertNotIn("hotkey", data)  # a short status, not this PC's setup
        for method, path in [("GET", "/v1/settings"), ("PUT", "/v1/settings"), ("GET", "/v1/tools"),
                             ("GET", "/v1/tool_files/open_youtube"), ("DELETE", "/v1/tool_files/open_youtube"),
                             ("GET", "/v1/history"), ("GET", "/v1/training_data"), ("GET", "/v1/pending"),
                             ("POST", "/v1/pending/abcd1234/confirm"), ("GET", "/v1/profiles"), ("GET", "/v1/keys"),
                             ("POST", "/v1/keys"), ("POST", "/v1/settings/regenerate_token"), ("POST", "/v1/open/data")]:
            self.assertEqual(self.call(method, path, {} if method in ("PUT", "POST") else None, token=key)[0], 403,
                             f"{method} {path} let a program key through")

    def test_program_tools_belong_to_the_key(self):
        key = self.make_key("own_robot")["key"]
        tools = [{"name": "own_robot_walk", "description": "Walk the test robot.", "parameters": {}}]
        status, data = self.call("POST", "/v1/tools", {"callback": "http://127.0.0.1:9/", "tools": tools}, token=key)
        self.assertEqual(status, 200, data)
        self.assertEqual(self.grain.registry.get("own_robot_walk").origin, "own_robot")
        self.assertEqual(self.call("GET", "/v1/status", token=key)[1]["tools"], ["own_robot_walk"])
        # Not someone else's name, not with the tool editor key, and not someone else's tools.
        self.assertEqual(self.call("POST", "/v1/tools", {"client": "other_robot", "callback": "http://127.0.0.1:9/",
                                                          "tools": tools}, token=key)[0], 403)
        self.assertEqual(self.call("POST", "/v1/tools", {"client": "own_robot", "callback": "http://127.0.0.1:9/",
                                                          "tools": tools})[0], 403)
        self.assertEqual(self.call("DELETE", "/v1/tools?client=other_robot", token=key)[0], 403)
        self.assertEqual(self.call("DELETE", "/v1/tools", token=key)[0], 200)
        self.assertIsNone(self.grain.registry.get("own_robot_walk"))

    def test_revoking_a_key_stops_it_and_removes_its_tools(self):
        made = self.make_key("revoked_robot")
        tools = [{"name": "revoked_robot_wave", "description": "Wave the test robot.", "parameters": {}}]
        self.call("POST", "/v1/tools", {"callback": "http://127.0.0.1:9/", "tools": tools}, token=made["key"])
        self.assertIsNotNone(self.grain.registry.get("revoked_robot_wave"))
        self.assertEqual(self.call("DELETE", f"/v1/keys/{made['id']}")[0], 200)
        self.assertEqual(self.call("GET", "/v1/status", token=made["key"])[0], 401)
        self.assertIsNone(self.grain.registry.get("revoked_robot_wave"))

    def test_unknown_and_malformed_keys_are_refused(self):
        made = self.make_key("real_robot")
        forged = made["key"][:-4] + ("AAAA" if not made["key"].endswith("AAAA") else "BBBB")
        for token in ["", "nonsense", forged, "grk_00000000_" + "x" * 32]:
            self.assertEqual(self.call("GET", "/v1/status", token=token)[0], 401, token)

    def test_program_requests_are_labelled_and_their_events_are_their_own(self):
        key = self.make_key("events_robot")["key"]
        seq = self.call("GET", "/v1/events")[1]["seq"]
        self.call("POST", "/v1/interpret", {"text": "what time is it", "execute": False})
        status, data = self.call("POST", "/v1/interpret", {"text": "what time is it", "execute": False}, token=key)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["results"][0]["source"], "program:events_robot")
        mine = self.call("GET", f"/v1/events?after={seq}", token=key)[1]["events"]
        self.assertEqual([e["source"] for e in mine], ["program:events_robot"])
        everything = self.call("GET", f"/v1/events?after={seq}")[1]["events"]
        self.assertEqual(len(everything), 2)

    def test_bad_input_is_refused_not_a_crash(self):
        self.assertEqual(self.call("GET", "/v1/history?limit=lots")[0], 400)
        self.assertEqual(self.call("GET", "/v1/events?timeout=soon")[0], 400)
        self.assertEqual(self.call("POST", "/v1/keys", ["not", "an", "object"])[0], 400)
        self.assertEqual(self.raw("PUT", "/v1/settings", b"{}", {"Content-Length": str(2_000_000)})[0], 413)

    def test_the_port_is_held_exclusively(self):
        from grain.api import serve
        with self.assertRaises(OSError):
            serve(self.grain, self.port)  # another server can't share Grain's port
        squatter = socket.socket()
        squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with self.assertRaises(OSError):
            squatter.bind(("127.0.0.1", self.port))
        squatter.close()


class ToolFileDefaults(unittest.TestCase):
    def test_spoken_paths_and_typing_ask_first(self):
        from grain.filetools import default_safety
        self.assertEqual(default_safety({"type": "open", "target": "https://www.youtube.com/results?search_query={query}"}), "run")
        self.assertEqual(default_safety({"type": "open", "target": "notepad.exe"}), "run")
        self.assertEqual(default_safety({"type": "open", "target": "{path}"}), "confirm")
        self.assertEqual(default_safety({"type": "open", "target": "C:/Projects/{name}"}), "confirm")
        self.assertEqual(default_safety({"type": "keys", "keys": "windows+shift+s"}), "run")
        self.assertEqual(default_safety({"type": "keys", "text": "{message}"}), "confirm")
        self.assertEqual(default_safety({"type": "powershell", "script": "Get-Date"}), "confirm")
        self.assertEqual(default_safety({"type": "http", "url": "http://127.0.0.1:8790/{x}"}), "run")


class Updates(unittest.TestCase):
    def serve_manifest(self, manifest):
        body = json.dumps(manifest).encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/latest.json"

    def test_newer_version_found(self):
        url = self.serve_manifest({"version": "9.0.0", "url": "https://example.com/grain-9.exe", "notes": "big"})
        result = settings.check_for_update({"update_url": url})
        self.assertTrue(result["available"])
        self.assertEqual(result["latest"], "9.0.0")

    def test_same_version_is_up_to_date(self):
        url = self.serve_manifest({"version": __version__})
        self.assertFalse(settings.check_for_update({"update_url": url})["available"])

    def test_no_address_means_no_request(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            self.assertFalse(settings.check_for_update({"update_url": ""})["available"])

    def test_update_address_must_be_https_unless_local(self):
        self.assertEqual(settings.validate({"update_url": "https://example.com/latest.json"})[1], {})
        self.assertEqual(settings.validate({"update_url": "http://127.0.0.1:9000/latest.json"})[1], {})
        self.assertIn("update_url", settings.validate({"update_url": "http://example.com/latest.json"})[1])
        self.assertIn("update_url", settings.validate({"update_url": "http://127.0.0.1.example.com/x"})[1])
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            result = settings.check_for_update({"update_url": "http://example.com/latest.json"})  # saved before the rule
        self.assertFalse(result["available"])


class UninstallerLeftovers(unittest.TestCase):
    @unittest.skipUnless(platform.IS_WINDOWS, "the installer writes registry keys and Start-menu shortcuts")
    def test_only_old_grain_unpack_folders_are_recognised(self):
        sys.path.insert(0, os.path.join(ROOT, "installer"))
        import grain_setup
        temp = tempfile.mkdtemp()
        old = 1_600_000_000  # long ago

        def folder(name, files, age=old):
            path = os.path.join(temp, name)
            for f in files:
                os.makedirs(os.path.dirname(os.path.join(path, f)), exist_ok=True)
                write(os.path.join(path, f), "")
            os.utime(path, (age, age))
            return path

        grain_app = folder("_MEI00008a302", [os.path.join("grain", "ui", "index.html")])  # hex name, like real ones
        installer = folder("_MEI0000628c2", ["grain.png", "version.txt"])
        folder("_MEI0000abc12", ["someone_elses_app.dll"])  # another program's: never touched
        folder("_MEI0000def34", [os.path.join("grain", "ui", "index.html")], age=time.time())  # just made: maybe in use
        folder("not_MEI1", [os.path.join("grain", "ui", "index.html")])
        with mock.patch.object(grain_setup.tempfile, "gettempdir", lambda: temp):
            found = sorted(grain_setup.grain_unpack_folders())
        self.assertEqual(found, sorted([grain_app, installer]))


class SingleInstance(unittest.TestCase):
    def test_second_claim_fails(self):
        from grain import app
        name = f"GrainTest-{os.getpid()}"  # the same mechanism claim_single_instance uses, under a test-only name
        self.assertTrue(platform.claim_single_instance(name))  # first claim wins
        self.assertFalse(platform.claim_single_instance(name))  # a second one is refused
        self.assertTrue(callable(app.claim_single_instance))


if __name__ == "__main__":
    unittest.main()
