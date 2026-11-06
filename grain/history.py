"""The correction log: every request, what Needle chose, and what the user says it should
have chosen. Corrected entries export as training data in the Needle platform's chat format."""
import json
import os
import threading
import time
import uuid


class History:
    def __init__(self, path, keep=2000):
        self.path, self.keep = path, keep
        self._lock = threading.Lock()
        self._entries = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    try:
                        self._entries.append(json.loads(line))
                    except ValueError:
                        pass

    def _write(self):
        self._entries = self._entries[-self.keep:]
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(e) + "\n" for e in self._entries)

    def add(self, **entry):
        entry = {"id": uuid.uuid4().hex[:10], "time": time.time(), "correction": None, **entry}
        with self._lock:
            self._entries.append(entry)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry) + "\n")
        return entry

    def update(self, entry_id, **fields):
        with self._lock:
            for entry in self._entries:
                if entry["id"] == entry_id:
                    entry.update(fields)
                    self._write()
                    return entry
        raise KeyError(entry_id)

    def recent(self, limit=100):
        with self._lock:
            return list(reversed(self._entries[-limit:]))

    def training_examples(self, registry):
        """One chat-format example per corrected entry, using today's schemas for the tools it saw.
        A correction of [] teaches the model to make no call."""
        examples = []
        with self._lock:
            entries = [e for e in self._entries if e.get("correction") is not None]
        for entry in entries:
            tools = [registry.get(n) for n in entry.get("tools", [])]
            tools = [t for t in tools if t is not None]
            calls = entry["correction"]
            if not tools or any(registry.get(c["name"]) is None for c in calls):
                continue
            reasoning = "; ".join(f"{c['name']}({json.dumps(c['arguments'])})" for c in calls) or "no tool fits this request"
            examples.append({
                "messages": [
                    {"role": "user", "content": entry["text"]},
                    {"role": "assistant", "content": "", "reasoning_content": reasoning,
                     "tool_calls": [{"id": f"call_{i + 1}", "type": "function",
                                     "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                                    for i, c in enumerate(calls)]}],
                "tools": [{"type": "function", "function": t.schema()} for t in tools]})
        return examples
