"""Remote tools: another program on this computer registers tools over the API, and Grain
calls it back when Needle picks one.

The callback receives POST {"tool": name, "arguments": {...}} and may answer {"result": "text"}.
"""
import json
import urllib.parse
import urllib.request

from .filetools import to_schema
from .registry import JSON_TYPES, NAME_PATTERN, SAFETY, Tool

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def check_callback(url):
    """Callbacks must stay on this machine, so a registration can't make Grain call out to the web."""
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "http" or parts.hostname not in LOCAL_HOSTS:
        raise ValueError("callback must be an http:// URL on 127.0.0.1 or localhost")
    return url


def remote_tools(client, callback, specs):
    check_callback(callback)

    if not isinstance(specs, list):
        raise ValueError("tools must be a list")

    def clean_spec(spec):
        if not isinstance(spec, dict):
            raise ValueError("each remote tool must be an object")
        name, description = spec.get("name"), spec.get("description")
        if not isinstance(name, str) or not NAME_PATTERN.match(name):
            raise ValueError("remote tool names must be snake_case")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"remote tool {name!r} needs a description")
        safety = spec.get("safety", "run")
        if safety not in SAFETY:
            raise ValueError(f"{name}: safety must be one of {', '.join(SAFETY)}")
        threshold = spec.get("threshold")
        if threshold is not None and (isinstance(threshold, bool) or not isinstance(threshold, (int, float))
                                      or not 0 <= threshold <= 1):
            raise ValueError(f"{name}: threshold must be a number between 0 and 1")
        for key in ("keywords", "phrases"):
            if key in spec and not isinstance(spec[key], list):
                raise ValueError(f"{name}: {key} must be a list")
        parameters = to_schema(spec.get("parameters", {}))
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError(f"{name}: parameters must be an object schema")
        for parameter, schema in parameters.get("properties", {}).items():
            if not isinstance(schema, dict) or schema.get("type", "string") not in JSON_TYPES.values():
                raise ValueError(f"{name}: parameter {parameter!r} has an invalid type")
        return {**spec, "parameters": parameters}

    specs = [clean_spec(spec) for spec in specs]

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

    return [Tool(name=s["name"], description=s["description"],
                  parameters=s["parameters"], handler=caller(s["name"]),
                 source="remote", safety=s.get("safety", "run"), threshold=s.get("threshold"),
                 origin=client, keywords=s.get("keywords", []), phrases=s.get("phrases", []))
            for s in specs]
