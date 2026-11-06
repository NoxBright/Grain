"""Phrases: spoken patterns that map straight to a tool call, checked before Needle.

Needle is small and weak at copying free text (a search query, a note) out of a short
command, so a tool can list patterns that fill its arguments directly:

    "search youtube for {query}"                       {query} takes whatever was said there
    {"say": "next song", "arguments": {"action": "next"}}   fixed arguments

Matching ignores case and punctuation and needs the whole utterance to fit. The longest
matching phrase wins. Number words ("two") fill integer and number parameters, and enum
parameters only accept one of their values.
"""
import re

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "zero": 0,
}
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def normalize(text):
    """Lowercase words separated by single spaces; keeps apostrophes and decimal points."""
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text.lower())
    return " ".join(re.sub(r"[^\w\s'.]", " ", text).split())


def parse_number(value):
    """Digits or spoken numbers: "3", "1,000", "twenty five", "a hundred", "one and a half". None if not a number."""
    text = str(value).strip().lower().replace(",", "")
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return float(text)
    words = [w for w in re.split(r"[\s-]+", text) if w and w != "and"]
    half = "half" in words
    words = [w for w in words if w != "half"]
    if half and all(w in ("a", "an") for w in words):
        return 0.5
    if not words:
        return None
    total, current = 0, 0
    for word in words:
        if word in ("a", "an"):
            current = current or 1
        elif word == "hundred":
            current = (current or 1) * 100
        elif word == "thousand":
            total, current = total + (current or 1) * 1000, 0
        elif word in NUMBER_WORDS:
            current += NUMBER_WORDS[word]
        elif re.fullmatch(r"\d+(\.\d+)?", word):
            current += float(word)
        else:
            return None
    return total + current + (0.5 if half else 0)


def compile_phrase(phrase, parameters):
    """A regex for one phrase plus its fixed arguments. Raises ValueError for unknown placeholders."""
    if isinstance(phrase, dict):
        say, fixed = phrase.get("say", ""), dict(phrase.get("arguments", {}))
    else:
        say, fixed = str(phrase), {}
    properties = parameters.get("properties", {})
    pattern, loose, literal = [], [], 0
    for index, part in enumerate(PLACEHOLDER.split(say)):
        if index % 2:  # a placeholder name
            if part not in properties:
                raise ValueError(f"phrase {say!r} uses {{{part}}}, which is not a parameter")
            pattern.append(f"(?P<{part}>.+?)")
            loose.append(f"(?P<{part}>.+?)")
        else:
            words = normalize(part)
            literal += len(words)
            if words:
                pattern.append(re.escape(words).replace(r"\ ", " "))
                loose.append(r"[^\w']+".join(re.escape(w) for w in words.split()))
    for name in fixed:
        if name not in properties:
            raise ValueError(f"phrase {say!r} sets {name!r}, which is not a parameter")
    if not literal:
        raise ValueError(f"phrase {say!r} needs at least one word besides placeholders")
    # The loose form runs on the original text, so free-text values keep their case and punctuation.
    loose_regex = re.compile(r"[^\w']*" + r"[^\w']+".join(loose) + r"[^\w']*", re.IGNORECASE)
    return re.compile(" ".join(pattern)), loose_regex, fixed, literal


def coerce(value, spec):
    """A spoken value as the parameter's type, or None if it doesn't fit."""
    kind, enum = spec.get("type", "string"), spec.get("enum")
    if enum:
        wanted = normalize(value).replace(" ", "_")
        for option in enum:
            if normalize(str(option)).replace(" ", "_") == wanted:
                return option
        number = parse_number(value)
        if number is not None and str(int(number)) in [str(o) for o in enum]:
            return next(o for o in enum if str(o) == str(int(number)))
        return None
    if kind in ("integer", "number"):
        number = parse_number(value)
        if number is None:
            return None
        return int(round(number)) if kind == "integer" else number
    if kind == "boolean":
        return {"yes": True, "on": True, "true": True, "no": False, "off": False, "false": False}.get(normalize(value))
    return value.strip()


def coerce_value(value, spec):
    """A value from Needle's JSON as the parameter's type, or None if it doesn't fit."""
    kind = spec.get("type", "string")
    if isinstance(value, bool):
        return value if kind == "boolean" and not spec.get("enum") else None
    if kind in ("array", "object"):
        return value if isinstance(value, (list, dict)) else None
    if isinstance(value, (int, float)) and not spec.get("enum"):
        if kind == "integer":
            return int(round(value))
        if kind == "number":
            return float(value)
    if isinstance(value, (list, dict)):
        return None
    return coerce(str(value), spec)


def clean_arguments(tool, arguments):
    """(clean arguments, None), or (None, what's wrong). Invented arguments are dropped,
    empty ones count as not given, and every value is converted to its parameter's type."""
    properties = tool.parameters.get("properties", {})
    cleaned = {}
    for name, value in (arguments or {}).items():
        spec = properties.get(name)
        if spec is None or value is None or (isinstance(value, str) and not value.strip()):
            continue
        typed = coerce_value(value, spec)
        if typed is None or typed == "":
            allowed = f" (one of {', '.join(map(str, spec['enum']))})" if spec.get("enum") else ""
            return None, f"got {name}={value!r}, which isn't a valid {spec.get('type', 'string')}{allowed}"
        cleaned[name] = typed
    missing = [n for n in tool.parameters.get("required", []) if n not in cleaned]
    if missing:
        return None, "needs " + " and ".join(missing)
    return cleaned, None


class PhraseMatcher:
    def __init__(self, tools):
        self.entries = []
        for tool in tools:
            for phrase in tool.phrases:
                regex, loose, fixed, literal = compile_phrase(phrase, tool.parameters)
                self.entries.append((literal, tool, regex, loose, fixed))
        self.entries.sort(key=lambda e: -e[0])  # most specific first

    def match(self, text):
        """(tool, arguments) for the best matching phrase, or None."""
        spoken = normalize(text)
        for _, tool, regex, loose, fixed in self.entries:
            found = regex.fullmatch(spoken)
            if not found:
                continue
            original = loose.fullmatch(text.strip())
            properties = tool.parameters.get("properties", {})
            arguments, ok = dict(fixed), True
            for name, value in found.groupdict().items():
                spec = properties.get(name, {})
                if original and spec.get("type", "string") == "string" and not spec.get("enum"):
                    value = original.group(name).strip(" .,!?")
                typed = coerce(value, spec)
                if typed is None:
                    ok = False
                    break
                arguments[name] = typed
            if ok and tool.check is not None:
                try:
                    ok = bool(tool.check(arguments))
                except Exception:
                    ok = False
            if ok:
                return tool, arguments
        return None
