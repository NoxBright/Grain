"""Grain App entry point.

    pythonw grain_app.py                          tray app (or run Grain App.exe)
    python grain_app.py --text "open notepad" --dry-run
    python grain_app.py --wav clip.wav --dry-run
"""
import os
import sys

FROZEN = getattr(sys, "frozen", False)

if FROZEN and sys.argv[2:3] == ["--child"]:
    # Needle runs its engine in a child started as `sys.executable _worker.py --child`.
    # Inside the exe that is this exe, so serve as that worker instead of opening another
    # tray app. A windowed exe gets no sys.stdin/stdout, so rebuild them from the pipes.
    import io
    sys.stdin = io.TextIOWrapper(io.FileIO(0, "rb", closefd=False))
    sys.stdout = io.TextIOWrapper(io.FileIO(1, "wb", closefd=False))
    sys.stderr = open(os.devnull, "w")
    from needle._worker import _child
    sys.exit(_child())

os.environ.setdefault("NEEDLE_TELEMETRY", "0")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
_engine = os.path.join(getattr(sys, "_MEIPASS", ""), "libneedle.dll")
if FROZEN and os.path.exists(_engine):  # the exe carries its own engine
    os.environ.setdefault("NEEDLE3_LIB_PATH", _engine)
if FROZEN or sys.stdout is None:  # windowed exe and pythonw have no console to print to
    sys.stdout = sys.stderr = open(os.devnull, "w")

import grain.offline  # noqa: E402,F401  (before anything can import needle or huggingface_hub)
from grain.app import main  # noqa: E402  (after the worker check, which must stay light)

if __name__ == "__main__":
    main()
