"""Example remote-tool client: a separate program that gives Grain new tools over its API.

Make a program key for it first: Grain's tool editor → Settings → Program keys, program name
robot_example. Then run it while Grain App is running:

    set GRAIN_API_KEY=grk_...
    python examples\\remote_tool_client.py

GRAIN_PORT is the API port if you changed it (default 8765).

It registers two robot tools, then serves the callback Grain uses when Needle picks one.
Swap the print calls for your robot's real control code.
"""
import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

GRAIN_PORT = int(os.environ.get("GRAIN_PORT", "8765"))
CALLBACK_PORT = 8790

TOOLS = [
    {"name": "walk", "description": "Walk the robot a number of steps in one direction.",
     "parameters": {"direction": {"type": "string", "enum": ["forward", "back", "left", "right"]},
                    "steps": {"type": "integer"}}},
    {"name": "set_gait", "description": "Switch the robot's walking gait.",
     "parameters": {"gait": {"type": "string", "enum": ["tripod", "wave", "ripple"]}},
     "keywords": ["gait"]},
]


def grain(method, path, body=None):
    key = os.environ.get("GRAIN_API_KEY")
    if not key:
        sys.exit("Set GRAIN_API_KEY to a program key from Grain's tool editor (Settings → Program keys).")
    request = urllib.request.Request(
        f"http://127.0.0.1:{GRAIN_PORT}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


class Callback(BaseHTTPRequestHandler):
    def do_POST(self):
        call = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        print("Grain asked for:", call["tool"], call["arguments"])  # drive the robot here
        reply = json.dumps({"result": f"robot did {call['tool']}"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(grain("POST", "/v1/tools", {"callback": f"http://127.0.0.1:{CALLBACK_PORT}/", "tools": TOOLS}))
    print(f"Serving callbacks on port {CALLBACK_PORT}. Ctrl+C to stop and unregister.")
    try:
        HTTPServer(("127.0.0.1", CALLBACK_PORT), Callback).serve_forever()
    except KeyboardInterrupt:
        grain("DELETE", "/v1/tools")
        sys.exit(0)
