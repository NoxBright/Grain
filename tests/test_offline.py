"""Grain must never reach the network: loading models, AI guesses and speech recognition all stay local.

Every socket connection to anything but this machine is refused and recorded.
"""
import copy
import os
import socket
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DATA = tempfile.mkdtemp(prefix="grain-offline-")
os.environ["GRAIN_DATA_DIR"] = DATA

import grain.offline  # noqa: E402,F401

attempts = []
_real_connect = socket.socket.connect


def _guarded_connect(self, address):
    host = address[0] if isinstance(address, tuple) else str(address)
    if host not in ("127.0.0.1", "::1", "localhost"):
        attempts.append(host)
        raise OSError(f"network blocked by test: {host}")
    return _real_connect(self, address)


class Offline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socket.socket.connect = _guarded_connect
        from grain.app import Grain
        from grain.config import DEFAULT_CONFIG, Config
        config = Config(copy.deepcopy(DEFAULT_CONFIG))
        config["api_token"] = "test"
        cls.grain = Grain(config, lambda message: None, history_path=os.path.join(DATA, "history.jsonl"))
        assert cls.grain.engine.ready.wait(60) and not cls.grain.engine.load_error

    @classmethod
    def tearDownClass(cls):
        socket.socket.connect = _real_connect

    def test_ai_guess_and_speech_stay_local(self):
        result = self.grain.engine.interpret("what's the time right now", execute=False, source="test").result(timeout=120)[0]
        self.assertIn(result["outcome"], ("dry_run", "low_confidence", "no_call"))
        clip = os.path.join(ROOT, "dev", "test_clip.wav")
        heard, _ = self.grain.engine.hear(clip, execute=False).result(timeout=120)
        self.assertIn("walk", heard.lower())
        self.assertEqual(attempts, [], f"Grain tried to reach: {sorted(set(attempts))}")

    def test_bundled_models_are_used(self):
        import needle
        from grain.config import NEEDLE_WEIGHTS, WHISTLE_WEIGHTS
        from needle.agent import fetch
        self.assertEqual(needle._base_weights_path(3), NEEDLE_WEIGHTS)
        self.assertEqual(needle._base_weights_path(fetch.WHISTLE), WHISTLE_WEIGHTS)


if __name__ == "__main__":
    unittest.main()
