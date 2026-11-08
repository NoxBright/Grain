"""The tool registry: one list of tools that every source adds to and Needle picks from."""
import inspect
import logging
import re
import threading
import typing
from dataclasses import dataclass, field

from .phrases import compile_phrase

log = logging.getLogger("grain")

SOURCES = ("builtin", "file", "plugin", "remote")
SAFETY = ("run", "confirm", "strict")  # strict: needs at least STRICT_THRESHOLD confidence
STRICT_THRESHOLD = 0.8
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}
SCHEMA_TYPES = set(JSON_TYPES.values())


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: typing.Callable
    source: str = "builtin"
    safety: str = "run"
    threshold: typing.Optional[float] = None
    origin: str = ""  # file path, plugin path or remote client name
    keywords: list = field(default_factory=list)  # extra words Whistle should expect
    phrases: list = field(default_factory=list)  # spoken patterns matched before Needle (see phrases.py)
    # Optional check(arguments) -> bool for phrase matches: False means "not for me", so a catch-all
    # phrase like "open {app}" lets the request fall through when {app} isn't a real program.
    check: typing.Optional[typing.Callable] = None

    def __post_init__(self):
        if not NAME_PATTERN.match(self.name):
            raise ValueError(f"tool name {self.name!r} must be snake_case: lowercase letters, digits and _")
        if self.safety not in SAFETY:
            raise ValueError(f"safety must be one of {', '.join(SAFETY)}")
        if not self.description.strip():
            raise ValueError(f"tool {self.name!r} needs a description")
        if self.threshold is not None and (isinstance(self.threshold, bool)
                                           or not isinstance(self.threshold, (int, float))
                                           or not 0 <= self.threshold <= 1):
            raise ValueError(f"tool {self.name!r} threshold must be a number between 0 and 1")
        if not isinstance(self.keywords, list) or not all(isinstance(word, str) for word in self.keywords):
            raise ValueError(f"tool {self.name!r} keywords must be a list of strings")
        self._validate_parameters()
        for phrase in self.phrases:
            compile_phrase(phrase, self.parameters)  # raises on a bad phrase

    def _validate_parameters(self):
        if not isinstance(self.parameters, dict) or self.parameters.get("type") != "object":
            raise ValueError(f"tool {self.name!r} parameters must be an object schema")
        properties = self.parameters.get("properties", {})
        required = self.parameters.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list) \
                or not all(isinstance(name, str) for name in required):
            raise ValueError(f"tool {self.name!r} has an invalid parameter schema")
        if not all(name in properties for name in required):
            raise ValueError(f"tool {self.name!r} requires an unknown parameter")
        for name, spec in properties.items():
            if not isinstance(name, str) or not isinstance(spec, dict) \
                    or spec.get("type", "string") not in SCHEMA_TYPES:
                raise ValueError(f"tool {self.name!r} parameter {name!r} has an invalid type")
            if "enum" in spec and (not isinstance(spec["enum"], list)
                                    or not spec["enum"]):
                raise ValueError(f"tool {self.name!r} parameter {name!r} has an invalid enum")

    def schema(self):
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def info(self):
        return {"name": self.name, "description": self.description, "parameters": self.parameters,
                "source": self.source, "safety": self.safety, "threshold": self.threshold, "origin": self.origin,
                "phrases": self.phrases, "keywords": self.keywords}

    def spoken_words(self):
        """Enum values, phrase words and the name, written the way people say them, for Whistle's keyword biasing."""
        words = [self.name.replace("_", " ")] + list(self.keywords)
        for phrase in self.phrases:
            say = phrase.get("say", "") if isinstance(phrase, dict) else phrase
            words += [w for w in re.sub(r"\{\w+\}", " ", say).split() if len(w) > 3]
        for spec in self.parameters.get("properties", {}).values():
            words += [str(value).replace("_", " ") for value in spec.get("enum", [])]
        return words


class ToolRegistry:
    def __init__(self):
        self._tools = {}
        self._lock = threading.RLock()
        self.version = 0  # bumps on every change, so Needle agents know to rebuild

    def replace(self, source, tools, origin=None):
        """Swap every tool from one source (and origin, if given) for a new set.
        Returns the error messages for tools that were skipped."""
        errors = []
        with self._lock:
            for name in [n for n, t in self._tools.items()
                         if t.source == source and (origin is None or t.origin == origin)]:
                del self._tools[name]
            for tool in tools:
                existing = self._tools.get(tool.name)
                if existing is not None:
                    errors.append(f"{tool.name}: already defined by {existing.source} ({existing.origin or 'built in'})")
                    continue
                self._tools[tool.name] = tool
            self.version += 1
        for error in errors:
            log.warning("tool skipped: %s", error)
        return errors

    def get(self, name):
        with self._lock:
            return self._tools.get(name)

    def all(self):
        with self._lock:
            return sorted(self._tools.values(), key=lambda t: (SOURCES.index(t.source), t.name))


# ---------------------------------------------------------------- the @tool decorator

_pending = []


def schema_from_function(function):
    """JSON schema for a function's parameters, from its type hints and defaults."""
    hints = typing.get_type_hints(function)
    properties, required = {}, []
    for name, parameter in inspect.signature(function).parameters.items():
        hint = hints.get(name, str)
        spec = {}
        if typing.get_origin(hint) is typing.Literal:
            spec = {"type": "string", "enum": [str(v) for v in typing.get_args(hint)]}
        elif typing.get_origin(hint) is typing.Union:  # Optional[x]
            inner = [a for a in typing.get_args(hint) if a is not type(None)]
            spec = {"type": JSON_TYPES.get(inner[0] if inner else str, "string")}
        else:
            spec = {"type": JSON_TYPES.get(typing.get_origin(hint) or hint, "string")}
        properties[name] = spec
        if parameter.default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


def tool(function=None, *, name=None, description=None, safety="run", threshold=None, keywords=(), phrases=(),
         check=None):
    """Turn a function into a Grain tool. Its docstring's first line is the description
    Needle reads, and its type hints become the parameters:

        from grain import tool
        from typing import Literal

        @tool(safety="confirm", phrases=["restart {service}"])
        def restart_service(service: Literal["web", "db"]):
            "Restart one of the local services."
            ...
            return "restarted " + service
    """
    def register(fn):
        _pending.append(Tool(
            name=name or fn.__name__,
            description=description or (inspect.getdoc(fn) or "").split("\n")[0],
            parameters=schema_from_function(fn), handler=fn, source="plugin",
            safety=safety, threshold=threshold, keywords=list(keywords), phrases=list(phrases), check=check))
        return fn
    return register(function) if function is not None else register


def take_pending():
    """Tools registered by @tool since the last call."""
    tools = list(_pending)
    _pending.clear()
    return tools
