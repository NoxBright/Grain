"""Grain's local HTTP API, also used by the tool editor UI.

Security:
  - It binds to 127.0.0.1 only, and holds its port exclusively, so no other program can listen
    on the same port and receive Grain's requests and keys.
  - Requests whose Host or Origin isn't Grain's own are refused. That stops web pages from
    reaching it through DNS rebinding or cross-site requests.
  - Every /v1 call needs "Authorization: Bearer <key>". The owner key (api_token) is the tool
    editor's and can do everything. Other programs use a program key made for them in the tool
    editor (see keys.py), which only reaches the routes marked "program" or "any" below.
"""
import json
import logging
import os
import re
import secrets
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, keys, platform, settings
from .config import (DATA_DIR, ICON_PATH, LOG_PATH, NOTES_PATH, PLUGINS_DIR, TOOLS_DIR, UI_DIR, USER_MODELS_DIR,
                     needle_weights)
from .filetools import save_tool_file
from .registry import NAME_PATTERN, SAFETY

log = logging.getLogger("grain")

MAX_BODY = 1_000_000  # bytes; tool files and registrations are a few KB


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


class Handler(BaseHTTPRequestHandler):
    server_version = "Grain"
    grain = None  # the running app, set by serve()

    def log_message(self, fmt, *args):
        log.debug("api: " + fmt, *args)

    # -------------------------------------------------- plumbing

    def _send(self, status, body, content_type="application/json", headers=()):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "invalid_request", "Content-Length must be a number")
        if length < 0 or length > MAX_BODY:
            raise ApiError(413, "too_large", f"request bodies are limited to {MAX_BODY} bytes")
        if not length:
            return {}
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            raise ApiError(400, "invalid_json", "request body is not valid JSON")
        if not isinstance(body, dict):
            raise ApiError(400, "invalid_json", "request body must be a JSON object")
        return body

    def _number(self, name, default, low, high, kind=int):
        try:
            value = kind(self.query.get(name, default))
        except ValueError:
            raise ApiError(400, "invalid_request", f"{name} must be a number")
        return max(low, min(value, high))

    def _check_origin(self):
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in allowed:
            raise ApiError(403, "bad_host", "requests must address 127.0.0.1 or localhost")
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://" + h for h in allowed}:
            raise ApiError(403, "bad_origin", "cross-site requests are not allowed")

    def _caller(self):
        """Who is calling: sets self.program to None for the owner key, or to the program key's entry."""
        given = self.headers.get("Authorization", "")
        given = given[len("Bearer "):] if given.startswith("Bearer ") else ""
        if given and secrets.compare_digest(given.encode(), str(self.grain.config["api_token"]).encode()):
            self.program = None
            return
        self.program = keys.find(self.grain.config, given)
        if self.program is None:
            log.warning("api: refused a request with a missing or unknown key")
            raise ApiError(401, "unauthorized", "missing or unknown key: programs need a program key, made in "
                                                "Grain's tool editor under Settings → Program keys")

    def _allowed(self, access):
        if access == "owner" and self.program is not None:
            raise ApiError(403, "owner_only", "program keys can't use this; only Grain's tool editor can")
        if access == "program" and self.program is None:
            raise ApiError(403, "program_key_needed", "use a program key for this, made in Grain's tool editor "
                                                      "under Settings → Program keys")

    @property
    def client(self):
        return self.program["client"] if self.program else None

    def _dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        self.query = dict(urllib.parse.parse_qsl(url.query))
        try:
            self._check_origin()
            if method == "GET" and url.path in ("/", "/ui", "/ui/", "/ui/index.html"):
                return self._ui(url.path)
            if method == "GET" and url.path == "/ui/icon.png":
                with open(ICON_PATH, "rb") as handle:
                    return self._send(200, handle.read(), "image/png")
            if not url.path.startswith("/v1/"):
                raise ApiError(404, "not_found", "no such endpoint")
            self._caller()
            for pattern, verb, handler, access in ROUTES:
                match = re.fullmatch(pattern, url.path)
                if match and verb == method:
                    self._allowed(access)
                    result = handler(self, *match.groups())
                    if result is not None:
                        self._send(200, result)
                    return
            raise ApiError(404, "not_found", f"no {method} {url.path}")
        except ApiError as error:
            self._send(error.status, {"error": {"code": error.code, "message": str(error)}})
        except Exception as error:
            log.exception("api %s %s failed", method, self.path)
            self._send(500, {"error": {"code": "internal", "message": str(error)}})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _ui(self, path):
        if path in ("/", "/ui"):
            self.send_response(302)
            self.send_header("Location", "/ui/")
            self.end_headers()
            return
        with open(os.path.join(UI_DIR, "index.html"), "rb") as handle:
            self._send(200, handle.read(), "text/html; charset=utf-8",
                       headers=[("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                 "script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")])

    # -------------------------------------------------- endpoints

    def status(self):
        grain = self.grain
        if self.program:  # a program sees what it needs, not the setup of this PC
            return {"app": "Grain", "version": __version__, "client": self.client, "profile": grain.profile(),
                    "tools": [t.name for t in grain.registry.all() if t.source == "remote" and t.origin == self.client]}
        active = grain.active_tools()
        return {"app": "Grain", "profile": grain.profile(), "active_tools": [t.name for t in active],
                "model": os.path.basename(needle_weights(grain.config)), "api_port": grain.config["api_port"],
                "hotkey": grain.config["hotkey"], "wake_words": grain.config["wake_words"],
                "confidence_threshold": grain.config["confidence_threshold"], "confirm_seconds": grain.config["confirm_seconds"],
                "version": __version__, "setup_done": bool(grain.config.get("setup_done")),
                "tool_count": len(grain.registry.all()), "too_many_tools": len(active) > grain.TOO_MANY_TOOLS,
                "pending": len(grain.engine.pending_list()), "mic": grain.mic_name, "load_errors": grain.load_errors}

    def list_tools(self):
        active = {t.name for t in self.grain.active_tools()}
        return {"tools": [dict(t.info(), active=t.name in active) for t in self.grain.registry.all()]}

    def register_remote(self):
        """Program keys only: the tools always belong to the key's program."""
        body = self._body()
        client, callback, tools = body.get("client", self.client), body.get("callback"), body.get("tools")
        if client != self.client:
            raise ApiError(403, "wrong_client", f"this key belongs to {self.client}, so it can only register {self.client}'s tools")
        if not callback or not isinstance(tools, list) or not all(isinstance(t, dict) for t in tools):
            raise ApiError(400, "invalid_request", "send callback and a list of tools")
        try:
            errors = self.grain.set_remote(client, callback, tools)
        except ValueError as error:
            raise ApiError(400, "invalid_request", str(error))
        return {"registered": [t["name"] for t in tools if not any(e.startswith(t.get("name", "") + ":") for e in errors)],
                "errors": errors}

    def remove_remote(self):
        client = self.query.get("client", self.client)
        if not client:
            raise ApiError(400, "invalid_request", "pass ?client=<name>")
        if self.program and client != self.client:
            raise ApiError(403, "wrong_client", f"this key belongs to {self.client}, so it can only remove {self.client}'s tools")
        self.grain.registry.replace("remote", [], origin=client)
        return {"removed": client}

    def _tool_path(self, name):
        if not NAME_PATTERN.match(name):
            raise ApiError(400, "invalid_name", "tool names are snake_case")
        return os.path.join(TOOLS_DIR, name + ".json")

    def get_tool_file(self, name):
        path = self._tool_path(name)
        if not os.path.exists(path):
            raise ApiError(404, "not_found", f"no tool file {name}.json")
        with open(path, encoding="utf-8-sig") as handle:
            return json.load(handle)

    def put_tool_file(self, name):
        old_path, data = self._tool_path(name), self._body()
        self._tool_path(data.get("name", ""))
        if data.get("safety") and data["safety"] not in SAFETY:
            raise ApiError(400, "invalid_request", f"safety must be one of {', '.join(SAFETY)}")
        existing = self.grain.registry.get(data["name"])
        if existing and existing.source != "file":
            raise ApiError(409, "name_taken", f"{data['name']} is already a {existing.source} tool")
        try:
            path = save_tool_file(TOOLS_DIR, data)
        except (ValueError, KeyError, TypeError) as error:
            raise ApiError(400, "invalid_tool", f"{type(error).__name__}: {error}")
        if os.path.abspath(path) != os.path.abspath(old_path) and os.path.exists(old_path):
            os.remove(old_path)  # renamed
        errors = self.grain.reload_files()
        return {"saved": data["name"], "errors": errors}

    def delete_tool_file(self, name):
        path = self._tool_path(name)
        if not os.path.exists(path):
            raise ApiError(404, "not_found", f"no tool file {name}.json")
        os.remove(path)
        self.grain.reload_files()
        return {"deleted": name}

    def interpret(self):
        body = self._body()
        text = str(body.get("text", "")).strip()
        if not text:
            raise ApiError(400, "invalid_request", "send text")
        profile = body.get("profile")
        if profile and profile not in self.grain.config["profiles"]:
            raise ApiError(400, "invalid_request", f"no profile {profile!r}")
        if self.program:  # its results are labelled as its own, and its words never approve a pending call
            future = self.grain.engine.interpret(text, execute=bool(body.get("execute", True)), profile=profile,
                                                 source=f"program:{self.client}", may_confirm=False)
        else:
            future = self.grain.engine.interpret(text, execute=bool(body.get("execute", True)), profile=profile,
                                                 source=str(body.get("source", "api"))[:32])
        return {"results": future.result(timeout=120)}

    def pending(self):
        return {"pending": self.grain.engine.pending_list()}

    def resolve_pending(self, pending_id, action):
        future = self.grain.engine.confirm(pending_id) if action == "confirm" else self.grain.engine.cancel(pending_id)
        return {"results": future.result(timeout=60)}

    def history(self):
        return {"entries": self.grain.history.recent(self._number("limit", 100, 1, 2000))}

    def correct(self, entry_id):
        calls = self._body().get("calls")
        if not isinstance(calls, list) or not all(isinstance(c, dict) and isinstance(c.get("name"), str)
                                                  and isinstance(c.get("arguments", {}), dict) for c in calls):
            raise ApiError(400, "invalid_request", "send calls: [{name, arguments}], or [] for 'no tool'")
        calls = [{"name": c["name"], "arguments": c.get("arguments", {})} for c in calls]
        try:
            return {"entry": self.grain.history.update(entry_id, correction=calls)}
        except KeyError:
            raise ApiError(404, "not_found", f"no history entry {entry_id}")

    def training_data(self):
        lines = "".join(json.dumps(e) + "\n" for e in self.grain.history.training_examples(self.grain.registry))
        self._send(200, lines.encode(), "application/x-ndjson",
                   headers=[("Content-Disposition", 'attachment; filename="grain-training.jsonl"')])

    def get_profiles(self):
        config = self.grain.config
        return {"active_profile": config["active_profile"], "current": self.grain.profile(),
                "profiles": config["profiles"], "profile_rules": config["profile_rules"]}

    def put_profiles(self):
        body, config = self._body(), self.grain.config
        profiles = body.get("profiles", config["profiles"])
        rules = body.get("profile_rules", config["profile_rules"])
        active = body.get("active_profile", config["active_profile"])
        if not isinstance(profiles, dict) or not profiles or not all(
                isinstance(p, list) and all(isinstance(x, str) for x in p) for p in profiles.values()):
            raise ApiError(400, "invalid_request", "profiles: {name: [tool name patterns]}")
        if not isinstance(rules, list) or not all(isinstance(r, dict) and r.get("profile") in profiles for r in rules):
            raise ApiError(400, "invalid_request", "profile_rules: [{process, profile}] naming existing profiles")
        if active != "auto" and active not in profiles:
            raise ApiError(400, "invalid_request", "active_profile must be 'auto' or a profile name")
        config.update(profiles=profiles, profile_rules=rules, active_profile=active)
        config.save()
        self.grain.on_profiles_changed()
        return self.get_profiles()

    def events(self):
        after = self._number("after", 0, 0, 2 ** 62)
        timeout = self._number("timeout", 0, 0.0, 30.0, kind=float)
        events = self.grain.events.since(after, timeout)
        if self.program:  # only the results of its own requests
            events = [e for e in events if e.get("source") == f"program:{self.client}"]
        return {"events": events, "seq": self.grain.events.seq}

    # -------------------------------------------------- program keys (owner only)

    def list_keys(self):
        return {"keys": keys.listing(self.grain.config)}

    def make_key(self):
        try:
            key, details = keys.issue(self.grain.config, self._body().get("client"))
        except ValueError as error:
            raise ApiError(400, "invalid_request", str(error))
        log.info("api: made a program key for %s", details["client"])
        return {"key": key, **details, "note": "This is the only time the key is shown. Give it to the program now."}

    def revoke_key(self, key_id):
        details = keys.revoke(self.grain.config, key_id)
        if details is None:
            raise ApiError(404, "not_found", f"no program key {key_id}")
        self.grain.registry.replace("remote", [], origin=details["client"])  # its tools go with it
        log.info("api: revoked the program key for %s", details["client"])
        return {"revoked": details}

    # -------------------------------------------------- settings and setup

    def _microphones(self):
        if self.grain.mic_names:
            return self.grain.mic_names
        from .audio import import_sounddevice, input_names
        return input_names(import_sounddevice())

    def get_settings(self):
        return {"settings": settings.public(self.grain.config), "microphones": self._microphones(),
                "microphone_in_use": self.grain.mic_name, "version": __version__,
                "restart_needed_for": sorted(settings.NEEDS_RESTART)}

    def put_settings(self):
        clean, problems = settings.validate(self._body())
        if problems:
            raise ApiError(400, "invalid_settings", "; ".join(f"{k} {v}" for k, v in problems.items()))
        restart = self.grain.apply_settings(clean)
        return dict(self.get_settings(), restart_needed=restart)

    def test_microphone(self):
        """Listens for three seconds on the chosen microphone, alongside Grain's own listening."""
        import numpy as np
        from .audio import Microphone
        device = self._body().get("device", self.grain.config.get("input_device"))
        mic = Microphone(device)
        peak, frames = 0.0, 0
        try:
            mic.open()
            while frames < int(3 / mic.FRAME_SECONDS):
                frame = mic.read(timeout=1)
                if frame is None:
                    break
                frames += 1
                peak = max(peak, float(np.sqrt(np.mean(frame ** 2))))
        finally:
            mic.close()
        if frames == 0:
            verdict = "No audio arrived at all. The microphone may be in use exclusively by another app, or unplugged."
        elif peak < 0.0005:
            verdict = "Total silence: this microphone isn't sending any sound. Check it's connected and not muted."
        elif peak < 0.008:
            verdict = "Very quiet. Grain may not notice you speaking; move closer or raise the mic level in Windows."
        else:
            verdict = "Sounds good."
        return {"device": mic.name, "peak": round(peak, 5), "level": min(1.0, peak / 0.1), "ok": peak >= 0.008,
                "verdict": verdict}

    def finish_setup(self):
        self.grain.config["setup_done"] = True
        self.grain.config.save()
        return {"setup_done": True}

    def regenerate_token(self):
        self.grain.config["api_token"] = secrets.token_urlsafe(24)
        self.grain.config.save()
        return {"api_token": self.grain.config["api_token"]}

    def open_folder(self, which):
        targets = {"data": DATA_DIR, "tools": TOOLS_DIR, "plugins": PLUGINS_DIR, "models": USER_MODELS_DIR,
                   "log": LOG_PATH, "notes": NOTES_PATH}
        path = targets.get(which)
        if path is None:
            raise ApiError(404, "not_found", f"no folder called {which}")
        if not os.path.exists(path):
            if os.path.splitext(path)[1]:
                open(path, "a", encoding="utf-8").close()
            else:
                os.makedirs(path, exist_ok=True)
        platform.open_path(path)
        return {"opened": which}

    def check_updates(self):
        try:
            return settings.check_for_update(self.grain.config)
        except Exception as error:
            raise ApiError(502, "update_check_failed", f"couldn't check for updates: {error}")


# (path, method, handler, who may call it): "owner" is the tool editor's key only, "program" a
# program key only, "any" either.
ROUTES = [
    (r"/v1/status", "GET", Handler.status, "any"),
    (r"/v1/tools", "GET", Handler.list_tools, "owner"),
    (r"/v1/tools", "POST", Handler.register_remote, "program"),
    (r"/v1/tools", "DELETE", Handler.remove_remote, "any"),
    (r"/v1/tool_files/([^/]+)", "GET", Handler.get_tool_file, "owner"),
    (r"/v1/tool_files/([^/]+)", "PUT", Handler.put_tool_file, "owner"),
    (r"/v1/tool_files/([^/]+)", "DELETE", Handler.delete_tool_file, "owner"),
    (r"/v1/interpret", "POST", Handler.interpret, "any"),
    (r"/v1/pending", "GET", Handler.pending, "owner"),
    (r"/v1/pending/([^/]+)/(confirm|cancel)", "POST", Handler.resolve_pending, "owner"),
    (r"/v1/history", "GET", Handler.history, "owner"),
    (r"/v1/history/([^/]+)/correction", "POST", Handler.correct, "owner"),
    (r"/v1/training_data", "GET", Handler.training_data, "owner"),
    (r"/v1/profiles", "GET", Handler.get_profiles, "owner"),
    (r"/v1/profiles", "PUT", Handler.put_profiles, "owner"),
    (r"/v1/events", "GET", Handler.events, "any"),
    (r"/v1/keys", "GET", Handler.list_keys, "owner"),
    (r"/v1/keys", "POST", Handler.make_key, "owner"),
    (r"/v1/keys/([0-9a-f]{8})", "DELETE", Handler.revoke_key, "owner"),
    (r"/v1/settings", "GET", Handler.get_settings, "owner"),
    (r"/v1/settings", "PUT", Handler.put_settings, "owner"),
    (r"/v1/settings/test_microphone", "POST", Handler.test_microphone, "owner"),
    (r"/v1/settings/regenerate_token", "POST", Handler.regenerate_token, "owner"),
    (r"/v1/setup/done", "POST", Handler.finish_setup, "owner"),
    (r"/v1/open/(\w+)", "POST", Handler.open_folder, "owner"),
    (r"/v1/updates/check", "POST", Handler.check_updates, "owner"),
]


class Server(ThreadingHTTPServer):
    # Python's default (SO_REUSEADDR) lets another program bind the same port on Windows and receive
    # some of Grain's requests, keys included. Grain holds its port exclusively instead, so a port
    # that's in use makes serve() fail and the app moves to the next free one.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def serve(grain, port):
    """Start the API on a background thread; returns the server, or raises OSError if the port is taken."""
    Handler.grain = grain
    server = Server(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, name="grain-api", daemon=True).start()
    log.info("API on http://127.0.0.1:%d", port)
    return server
