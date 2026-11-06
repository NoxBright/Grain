"""Python plugins: every .py file in plugins\\ that defines tools with @grain.tool.

Inside the exe, plugins run on its bundled Python, so they can use the standard library
and the packages Grain itself ships (numpy, PIL, keyboard, ...), but nothing installed separately.
"""
import importlib.util
import logging
import os

from .registry import take_pending

log = logging.getLogger("grain")


def load_folder(folder):
    tools, errors = [], []
    os.makedirs(folder, exist_ok=True)
    for filename in sorted(os.listdir(folder)):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        path = os.path.join(folder, filename)
        take_pending()  # drop anything a previous failed plugin half-registered
        try:
            spec = importlib.util.spec_from_file_location(f"grain_plugin_{filename[:-3]}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as error:
            errors.append(f"{filename}: {type(error).__name__}: {error}")
            log.exception("plugin %s failed to load", filename)
            continue
        for tool in take_pending():
            tool.origin = path
            tools.append(tool)
    return tools, errors
