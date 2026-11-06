"""Imported first by every test module: all temporary folders a test run makes go under one
parent folder, which is deleted when the run ends, so tests leave nothing behind in %TEMP%."""
import atexit
import os
import shutil
import tempfile

if not getattr(tempfile, "_grain_isolated", False):
    RUN_DIR = tempfile.mkdtemp(prefix="grain-test-run-")
    tempfile.tempdir = RUN_DIR
    tempfile._grain_isolated = True
    atexit.register(shutil.rmtree, RUN_DIR, True)
