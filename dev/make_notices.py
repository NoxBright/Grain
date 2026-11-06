"""Writes licenses\\ and THIRD_PARTY_NOTICES.txt for everything that ships inside Grain.

    python dev\\make_notices.py <output folder>
"""
import importlib.metadata as md
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (package, what it's for). Only what the build actually bundles; huggingface_hub and its
# network stack are deliberately left out of the build so Grain can't go online.
PACKAGES = [
    ("cactus-needle", "Needle and Whistle engine (tool calling, speech to text)"),
    ("numpy", "audio processing"),
    ("sounddevice", "microphone access"),
    ("cffi", "used by sounddevice"),
    ("pycparser", "used by cffi"),
    ("pystray", "system tray icon (LGPL-3.0: shipped as replaceable source in _internal\\pystray)"),
    ("six", "used by pystray"),
    ("pillow", "tray icon images"),
    ("keyboard", "global hotkey"),
]
MODELS = [("needle3-model", "Needle 3 model weights (models\\needle3.cact), by Cactus Compute"),
          ("whistle-model", "Whistle model weights (models\\whistle.cact), by Cactus Compute")]
PORTAUDIO = """PortAudio Portable Real-Time Audio Library
Copyright (c) 1999-2011 Ross Bencina, Phil Burk

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
associated documentation files (the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge, publish, distribute,
sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""


def license_files(package):
    return [f for f in (md.files(package) or [])
            if any(k in str(f).lower() for k in ("licen", "copying", "notice")) and str(f).count("/") <= 2]


def main(out):
    folder = os.path.join(out, "licenses")
    os.makedirs(folder, exist_ok=True)
    index = ["Grain itself is licensed under the MIT License (LICENSE.txt).", "",
             "Grain ships with the following third-party software. Full licence texts are in the",
             "licenses folder beside this file.", ""]

    def add(name, purpose, version, licence, texts):
        filename = f"{name}.txt"
        with open(os.path.join(folder, filename), "w", encoding="utf-8") as handle:
            handle.write("\n\n".join(texts))
        index.append(f"{name} {version}".strip())
        index.append(f"    {purpose}")
        index.append(f"    Licence: {licence}  (licenses\\{filename})")
        index.append("")

    python_license = os.path.join(sys.base_prefix, "LICENSE.txt")
    add("python", "the Python runtime", sys.version.split()[0], "PSF-2.0",
        [open(python_license, encoding="utf-8", errors="replace").read()])
    for package, purpose in PACKAGES:
        meta = md.metadata(package)
        licence = meta.get("License-Expression") or (meta.get("License") or "").splitlines()[0:1]
        licence = licence if isinstance(licence, str) else (licence[0] if licence else "see licence file")
        texts = [f.locate().read_text(encoding="utf-8", errors="replace") for f in license_files(package)]
        if not texts:
            raise SystemExit(f"no licence file found for {package}")
        add(package, purpose, meta["Version"], licence[:60], texts)
    add("portaudio", "audio I/O library used by sounddevice", "19", "MIT", [PORTAUDIO])
    for name, purpose in MODELS:
        text = open(os.path.join(ROOT, "dev", "licenses-src", f"{name}-LICENSE.txt"), encoding="utf-8").read()
        add(name, purpose, "", "Apache-2.0", [text])
    index += ["pystray is licensed under the LGPL-3.0. It is not compiled into Grain: its source files are in",
              "_internal\\pystray, and you may replace them with your own modified version. Its source is also",
              "available from https://github.com/moses-palmer/pystray."]
    with open(os.path.join(out, "THIRD_PARTY_NOTICES.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(index) + "\n")
    print(f"wrote notices for {len(PACKAGES) + len(MODELS) + 2} components")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "dist", "Grain"))
