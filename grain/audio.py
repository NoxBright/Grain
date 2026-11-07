"""Microphone capture in shared mode, with simple energy-based endpointing."""
import collections
import ctypes
import os
import queue
import shutil
import sys

from . import platform

RATE = 16000  # Whistle wants 16 kHz mono float


def import_sounddevice():
    """sounddevice's bundled PortAudio DLL can sit past Windows' 260-character path limit.
    If it won't load, copy it to a short folder as portaudio.dll, where sounddevice looks first.
    Linux loads its shared object directly, so there is no short path to make there."""
    if not platform.IS_WINDOWS:
        import sounddevice
        return sounddevice
    try:
        import sounddevice
        return sounddevice
    except OSError:
        pass
    import importlib.util
    package_dir = next(iter(importlib.util.find_spec("_sounddevice_data").submodule_search_locations))
    source = os.path.join(package_dir, "portaudio-binaries", "libportaudio64bit.dll")
    short = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW("\\\\?\\" + source, short, 1024)
    short_source = short.value.replace("\\\\?\\", "") or source
    lib_dir = os.path.join(os.environ["LOCALAPPDATA"], "Grain")
    os.makedirs(lib_dir, exist_ok=True)
    target = os.path.join(lib_dir, "portaudio.dll")
    if not os.path.exists(target):
        shutil.copyfile(short_source, target)
    os.environ["PATH"] = lib_dir + os.pathsep + os.environ["PATH"]
    sys.modules.pop("sounddevice", None)
    import sounddevice
    return sounddevice


def input_names(sd):
    """Microphones on the default host API (MME), so each one is listed once."""
    return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0
            and d["hostapi"] == sd.default.hostapi and "Sound Mapper" not in d["name"]]


def resolve_input(sd, wanted):
    """A device index for a name from input_names; other strings go to sounddevice's own matching."""
    if wanted is None or isinstance(wanted, int):
        return wanted
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0 and device["hostapi"] == sd.default.hostapi and device["name"] == wanted:
            return index
    return wanted


def rescan_devices(sd):
    """PortAudio lists devices once at start-up; restarting it picks up mics plugged in since."""
    sd._terminate()
    sd._initialize()


class Microphone:
    """Shared-mode capture: MME and shared WASAPI streams leave the device usable by
    every other app (calls, recorders, games) while Grain listens."""

    FRAME_SECONDS = 0.03

    def __init__(self, device=None):
        self.sd = import_sounddevice()
        self.missing = None  # the configured mic, when it wasn't found and the default stands in
        self.peak = 0.0  # loudest frame of the last capture; ~0 means the device sends no sound
        try:
            self.device = resolve_input(self.sd, device)
            info = self.sd.query_devices(self.device, kind="input")
        except ValueError:
            self.missing, self.device = device, None
            info = self.sd.query_devices(None, kind="input")
        self.name = info["name"]
        self.rate = int(info["default_samplerate"])
        hostapi = self.sd.query_hostapis(info["hostapi"])["name"]
        self.extra = self.sd.WasapiSettings(exclusive=False) if "WASAPI" in hostapi else None
        self.frames = queue.Queue()
        self.stream = None

    def open(self):
        if self.stream is None:
            self.frames = queue.Queue()
            self.stream = self.sd.InputStream(
                device=self.device, channels=1, samplerate=self.rate, dtype="float32",
                blocksize=int(self.rate * self.FRAME_SECONDS), extra_settings=self.extra,
                callback=lambda data, *_: self.frames.put(data[:, 0].copy()))
            self.stream.start()

    def close(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None

    def read(self, timeout=0.5):
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def to_16k(self, samples):
        import numpy as np
        if self.rate == RATE:
            return samples
        positions = np.arange(0, len(samples), self.rate / RATE)
        return np.interp(positions, np.arange(len(samples)), samples).astype(np.float32)


def capture_utterance(mic, config, should_stop, start_timeout=None):
    """Wait for speech, keep a short pre-roll, stop after a pause.
    Returns 16 kHz samples, or None if nobody spoke before start_timeout seconds."""
    import numpy as np
    frame_s = mic.FRAME_SECONDS
    preroll = collections.deque(maxlen=int(0.3 / frame_s))
    noise, speech, silent, waited = None, [], 0.0, 0.0
    mic.peak = 0.0
    while not should_stop():
        frame = mic.read()
        if frame is None:
            if start_timeout is not None:
                waited += 0.5  # read() waits half a second when PortAudio supplies no frame
                if waited > start_timeout:
                    return None
            continue
        rms = float(np.sqrt(np.mean(frame ** 2)))
        mic.peak = max(mic.peak, rms)
        noise = rms if noise is None else (min(noise * 1.002, rms) if not speech else noise)
        threshold = max(0.008, noise * 3.0)
        if not speech:
            preroll.append(frame)
            waited += frame_s
            if rms > threshold:
                speech = list(preroll)
            elif start_timeout is not None and waited > start_timeout:
                return None
            continue
        speech.append(frame)
        silent = silent + frame_s if rms < threshold else 0.0
        if silent >= config["silence_seconds"] or len(speech) * frame_s >= config["max_seconds"]:
            break
    if len(speech) * frame_s < 0.4:  # clicks and coughs
        return None
    return mic.to_16k(np.concatenate(speech))
