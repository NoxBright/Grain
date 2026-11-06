"""Everyday helpers that aren't part of Windows itself: web search, timers and notes."""
import datetime
import threading
import urllib.parse
import webbrowser

from grain import notify, tool
from grain.config import NOTES_PATH

SEARCH_URL = "https://duckduckgo.com/?q={}"
LONGEST_TIMER_HOURS = 24


@tool(phrases=["search for {query}", "search the web for {query}", "search {query}", "google {query}",
               "look up {query}"])
def search_web(query: str):
    "Search the web in the browser."
    query = query.strip()
    if not query:
        return "What should I search for?"
    webbrowser.open(SEARCH_URL.format(urllib.parse.quote_plus(query)))
    return f"searching for {query}"


def _describe(seconds):
    hours, rest = divmod(round(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    parts = [f"{hours} h" if hours else "", f"{minutes} min" if minutes else "", f"{secs} s" if secs else ""]
    return " ".join(p for p in parts if p) or "0 s"


@tool(keywords=["timer", "countdown", "alarm", "remind"],
      phrases=["set a timer for {minutes} minutes", "set a timer for {minutes} minute", "timer for {minutes} minutes",
               "{minutes} minute timer", "set a timer for {hours} hours", "set a timer for {hours} hour",
               "set a timer for {seconds} seconds", "{seconds} second timer",
               "set a timer for {hours} hours and {minutes} minutes", "set a timer for {hours} hour and {minutes} minutes",
               "set a timer for {minutes} minutes and {seconds} seconds",
               "set a {minutes} minute timer", "set a {minutes} minute timer for {label}",
               "set a timer for {minutes} minutes for {label}"])
def set_timer(minutes: float = 0, hours: float = 0, seconds: float = 0, label: str = "timer"):
    "Start a countdown timer that alerts when it ends."
    total = float(hours or 0) * 3600 + float(minutes or 0) * 60 + float(seconds or 0)
    if total <= 0:
        return "How long should the timer be?"
    if total > LONGEST_TIMER_HOURS * 3600:
        return f"Timers can be at most {LONGEST_TIMER_HOURS} hours"
    label = (label or "timer").strip()
    length = _describe(total)

    def ring():
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        notify(f"{label} is done ({length})")

    timer = threading.Timer(total, ring)
    timer.daemon = True
    timer.start()
    return f"{label} set for {length}"


@tool(keywords=["note", "jot", "write", "remember"],
      phrases=["take a note {text}", "take a note to {text}", "take a note that {text}",
               "note {text}", "make a note {text}", "make a note to {text}", "remember to {text}"])
def take_note(text: str):
    "Save a short note to the notes file."
    text = " ".join(text.split())
    if not text:
        return "What should the note say?"
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(NOTES_PATH, "a", encoding="utf-8") as handle:
        handle.write(f"- [{stamp}] {text}\n")
    return f"noted: {text}"
