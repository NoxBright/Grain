"""The router: text or speech in, tool calls out. Needle and Whistle are not thread-safe,
so one worker thread owns them and everything else hands it jobs."""
import collections
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from concurrent.futures import Future

from .config import WHISTLE_WEIGHTS, needle_weights
from .offline import lock_down
from .phrases import PhraseMatcher, clean_arguments, normalize
from .profiles import current_profile, select_tools
from .registry import STRICT_THRESHOLD

log = logging.getLogger("grain")

# A whole reply, not the start of one: "okay open notepad" is a new request, not a yes.
YES = re.compile(r"^(?:yes|yeah|yep|confirm|do it|go ahead|okay|ok)(?: please| do it| go ahead)?$")
NO = re.compile(r"^(?:no|nope|cancel|never ?mind|don'?t)(?: please| thanks| thank you)?$")
THEN = re.compile(r"[,.]?\s+(?:and\s+)?then\s+", re.IGNORECASE)


def summarize(calls):
    return ", ".join(f"{c['name']}({json.dumps(c['arguments'])})" for c in calls)


FILLER = {"the", "and", "for", "you", "your", "this", "that", "with", "what", "what's", "set", "take", "make", "turn",
          "please", "can", "could", "would", "open", "start", "run", "get", "put", "give", "show"}


def tool_words(tool):
    """The distinctive words that mean a request is about this tool."""
    words = set()
    for phrase in tool.spoken_words() + [p.get("say", "") if isinstance(p, dict) else p for p in tool.phrases]:
        words.update(w for w in normalize(re.sub(r"\{\w+\}", " ", phrase)).split() if len(w) > 2 and w not in FILLER)
    return words


def grounded(tool, text):
    """Whether the request mentions anything about the tool. Needle sometimes picks a tool with
    real confidence for a request that has nothing to do with it ("fire up steam" -> media
    controls); guesses that aren't grounded have to clear the strict threshold instead."""
    said = set(normalize(text).split())
    said |= {w[:-1] for w in said if w.endswith("s")}  # plurals: "videos" -> "video"
    return bool(tool_words(tool) & said)


def strip_wake_word(text, wake_words):
    """The request after the wake word, or None if the text doesn't start with one."""
    words = re.sub(r"[^\w\s']", " ", text.lower()).split()
    for wake in sorted(wake_words, key=len, reverse=True):
        wake_tokens = wake.lower().split()
        if words[:len(wake_tokens)] == wake_tokens:
            return " ".join(text.split()[len(wake_tokens):]).strip(" ,.")
    return None


class Events:
    """Recent results for the API's long-poll /v1/events."""

    def __init__(self, keep=200):
        self._cond = threading.Condition()
        self._items = collections.deque(maxlen=keep)
        self.seq = 0

    def publish(self, kind, data):
        with self._cond:
            self.seq += 1
            self._items.append({"seq": self.seq, "kind": kind, "time": time.time(), **data})
            self._cond.notify_all()

    def since(self, after, timeout=0.0):
        with self._cond:
            if timeout and self.seq <= after:
                self._cond.wait(timeout)
            return [e for e in self._items if e["seq"] > after]


class Engine:
    def __init__(self, config, registry, history, events):
        self.config, self.registry, self.history, self.events = config, registry, history, events
        self.pending = {}  # id -> calls waiting for a yes
        # The API and tray threads expire and list pending calls while the worker adds and resolves them.
        self._pending_lock = threading.RLock()
        self._agents = collections.OrderedDict()  # (registry version, tool names) -> Needle
        self._phrase_key, self._phrase_matcher = None, None
        self._jobs = queue.Queue()
        self._whistle = False
        self.load_error = None
        self.ready = threading.Event()
        threading.Thread(target=self._run, name="grain-engine", daemon=True).start()

    # -------------------------------------------------- worker thread

    def _run(self):
        try:
            import needle
            lock_down(needle)
            self.needle = needle
        except Exception as error:
            self.load_error = error
            log.exception("loading Needle failed")
        self.ready.set()
        while True:
            function, args, future = self._jobs.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                if self.load_error:
                    raise RuntimeError(f"Needle failed to load: {self.load_error}")
                future.set_result(function(*args))
            except BaseException as error:
                future.set_exception(error)

    def call(self, function, *args):
        """Run function(*args) on the worker thread; returns a Future."""
        future = Future()
        self._jobs.put((function, args, future))
        return future

    # -------------------------------------------------- public entry points (any thread)

    def interpret(self, text, execute=True, profile=None, source="api", may_confirm=True):
        """may_confirm=False: a "yes" in the text is a request like any other, never an OK for a
        pending call (programs on the API can't approve what Grain is waiting to ask you about)."""
        return self.call(self._handle, text, execute, profile, source, may_confirm)

    def hear(self, samples, require_wake=False, profile=None, execute=True):
        return self.call(self._hear, samples, require_wake, profile, execute)

    def confirm(self, pending_id):
        return self.call(self._confirm, pending_id)

    def cancel(self, pending_id):
        return self.call(self._cancel, pending_id)

    def pending_list(self):
        with self._pending_lock:
            self._expire()
            return [dict(p) for p in self.pending.values()]

    # -------------------------------------------------- the work

    def _agent(self, tools):
        key = (self.registry.version, tuple(t.name for t in tools))
        if key not in self._agents:
            if len(self._agents) >= 3:
                _, old = self._agents.popitem(last=False)
                old.close()
            self._agents[key] = self.needle.Needle(tools=[t.schema() for t in tools], weights=needle_weights(self.config),
                                                   stateless=True)
        self._agents.move_to_end(key)
        return self._agents[key]

    def _hear(self, samples, require_wake, profile, execute):
        if not self._whistle:
            self.needle.Whistle(weights=WHISTLE_WEIGHTS)
            self._whistle = True
        profile = profile or current_profile(self.config)
        keywords = []
        for tool in select_tools(self.registry.all(), self.config, profile):
            keywords += tool.spoken_words()
        if self.config["wake_word_mode"]:
            keywords += self.config["wake_words"]
        if self.pending:
            keywords += ["yes", "cancel"]
        heard = self.needle.transcribe(samples, language="en", keywords=list(dict.fromkeys(keywords))[:60])["text"].strip()
        log.debug("heard: %r", heard)  # what you said stays out of the log; History keeps it for review
        text = strip_wake_word(heard, self.config["wake_words"]) if require_wake else heard
        if not text:
            return heard, []
        return heard, self._handle(text, execute, profile, "voice")

    def _handle(self, text, execute, profile, source, may_confirm=True):
        text = text.strip()
        profile = profile or current_profile(self.config)
        with self._pending_lock:
            self._expire()
            latest = next((pending_id for pending_id in reversed(self.pending)
                           if not self.pending[pending_id]["source"].startswith("program:")), None) if may_confirm else None
        if latest and execute:
            spoken = " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())
            if YES.match(spoken):
                return self._confirm(latest)
            if NO.match(spoken):
                return self._cancel(latest)
        parts = [p.strip(" .,!?") for p in THEN.split(text)]
        return [self._one(p, execute, profile, source) for p in parts if p]

    def _bar(self, calls, text=""):
        bars = []
        for call in calls:
            tool = self.registry.get(call["name"])
            bar = self.config["tool_thresholds"].get(call["name"])
            if bar is None:
                bar = tool.threshold if tool and tool.threshold is not None else self.config["confidence_threshold"]
            if tool and (tool.safety == "strict" or (text and not grounded(tool, text))):
                bar = max(bar, STRICT_THRESHOLD)
            bars.append(bar)
        return max(bars)

    def _execute(self, calls):
        outputs = []
        for call in calls:
            tool = self.registry.get(call["name"])
            if tool is None:
                outputs.append(f"unknown tool {call['name']}")
                continue
            try:
                outputs.append(str(tool.handler(**call["arguments"])))
            except Exception as error:  # a tool failing should not take the assistant down
                log.exception("tool %s failed", call["name"])
                outputs.append(f"{call['name']} failed: {error}")
        return "; ".join(outputs)

    def _clean(self, calls):
        """Check Needle's calls against each tool's parameters before anything runs: drop invented
        arguments, convert types ("five" -> 5), and refuse unknown tools or missing required values."""
        cleaned = []
        for call in calls:
            tool = self.registry.get(call["name"])
            if tool is None:
                return [], f"there's no tool called {call['name']}"
            arguments, problem = clean_arguments(tool, call.get("arguments"))
            if problem:
                return calls, f"{call['name']} {problem}"
            cleaned.append({"name": tool.name, "arguments": arguments})
        return cleaned, None

    def _matcher(self, tools):
        key = (self.registry.version, tuple(t.name for t in tools))
        if self._phrase_key != key:
            self._phrase_key, self._phrase_matcher = key, PhraseMatcher(tools)
        return self._phrase_matcher

    def _one(self, text, execute, profile, source):
        active = select_tools(self.registry.all(), self.config, profile)
        calls, confidence, matched, problem = [], None, "needle", None
        # Phrases are exact, so they work whatever the profile; profiles only narrow what Needle guesses from.
        hit = self._matcher(self.registry.all()).match(text)
        if hit:
            calls, confidence, matched = [{"name": hit[0].name, "arguments": hit[1]}], 1.0, "phrase"
        elif active:
            reply = self._agent(active).complete(text)
            calls, confidence = reply["function_calls"], reply.get("confidence")
            calls, problem = self._clean(calls)
        log.debug("%r -> %s (%s, confidence %s, profile %s)", text, calls, matched, confidence, profile)
        unsure = calls and confidence is not None and confidence < self._bar(calls, "" if hit else text)
        if not hit and not active:
            outcome, message = "no_tools", f"No tools are active in profile '{profile}'"
        elif not calls and not problem:
            outcome, message = "no_call", f"No action for '{text}'"
        elif unsure:  # checked before problems: an unsure guess with bad arguments is still just unsure
            outcome, message = "low_confidence", f"Not sure ({confidence:.2f}) about '{text}': skipped {summarize(calls)}"
        elif problem:
            outcome, message = "invalid", f"Couldn't run that: {problem}"
        elif not execute:
            outcome, message = "dry_run", f"Would run {summarize(calls)}"
        elif any(getattr(self.registry.get(c["name"]), "safety", "run") == "confirm" for c in calls):
            outcome = "needs_confirm"
            message = f"Say 'yes' or confirm from the tray within {self.config['confirm_seconds']} s: {summarize(calls)}"
        else:
            outcome, message = "executed", self._execute(calls)
        entry = self.history.add(text=text, source=source, profile=profile, tools=[t.name for t in active],
                                 calls=calls, confidence=confidence, matched=matched, outcome=outcome, message=message)
        if outcome == "needs_confirm":
            pending_id = uuid.uuid4().hex[:8]
            with self._pending_lock:
                self.pending[pending_id] = {"id": pending_id, "text": text, "calls": calls, "summary": summarize(calls),
                                            "expires": time.time() + self.config["confirm_seconds"],
                                            "history_id": entry["id"], "source": source}
        result = {"id": entry["id"], "text": text, "profile": profile, "calls": calls, "confidence": confidence,
                  "matched": matched, "outcome": outcome, "message": message, "source": source}
        self.events.publish("result", result)
        return result

    def _expire(self):
        now = time.time()
        with self._pending_lock:
            expired = [self.pending.pop(i) for i, p in list(self.pending.items()) if p["expires"] < now]
        for item in expired:
            self.history.update(item["history_id"], outcome="expired")

    def _take(self, pending_id):
        with self._pending_lock:
            self._expire()
            return self.pending.pop(pending_id, None)

    def _confirm(self, pending_id):
        item = self._take(pending_id)
        if item is None:
            return [{"outcome": "nothing_pending", "message": "Nothing to confirm (it may have expired)"}]
        message = self._execute(item["calls"])
        self.history.update(item["history_id"], outcome="confirmed", message=message)
        result = {"id": item["history_id"], "text": item["text"], "calls": item["calls"], "outcome": "confirmed",
                  "message": message, "source": item["source"]}
        self.events.publish("result", result)
        return [result]

    def _cancel(self, pending_id):
        item = self._take(pending_id)
        if item is None:
            return [{"outcome": "nothing_pending", "message": "Nothing to cancel"}]
        self.history.update(item["history_id"], outcome="cancelled")
        result = {"id": item["history_id"], "text": item["text"], "calls": item["calls"], "outcome": "cancelled",
                  "message": f"Cancelled {item['summary']}", "source": item["source"]}
        self.events.publish("result", result)
        return [result]
