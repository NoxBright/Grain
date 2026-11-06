"""Remote tools: another program on this computer registers tools over the API, and Grain
calls it back when Needle picks one.

The callback receives POST {"tool": name, "arguments": {...}} and may answer {"result": "text"}.
"""
import json
import urllib.parse
import urllib.request

from .filetools import to_schema
from .registry import Tool

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def check_callback(url):
    """Callbacks must stay on this machine, so a registration can't make Grain call out to the web."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS:
        raise ValueError("callback must be an http:// URL on 127.0.0.1 or localhost")
    return url


def remote_tools(client, callback, specs):
    check_callback(callback)

    def caller(name):
        def handler(**arguments):
            request = urllib.request.Request(
                callback, data=json.dumps({"tool": name, "arguments": arguments}).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read(2000).decode("utf-8", "replace")
            try:
                return str(json.loads(body).get("result", "done"))
            except (ValueError, AttributeError):
                return body.strip()[:200] or "done"
        return handler

    return [Tool(name=s.get("name", ""), description=s.get("description", ""),
                 parameters=to_schema(s.get("parameters", {})), handler=caller(s.get("name", "")),
                 source="remote", safety=s.get("safety", "run"), threshold=s.get("threshold"),
                 origin=client, keywords=s.get("keywords", []), phrases=s.get("phrases", []))
            for s in specs]
