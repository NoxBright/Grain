# Changelog

## Unreleased

## 1.1.0 - 2026-11-09

Security:

- **Program keys.** Programs and scripts get a key of their own from the tool editor (Settings → Program keys) instead of using the tool editor's key from `settings.json`.
  - A program key can only register and remove its own tools, send requests, and read its own results and a short status.
  - It can't change settings or tools, read history, or approve pending calls.
  - Keys are shown once and stored only as SHA-256 fingerprints. Revoking one removes the program's tools.
  - Registering remote tools now needs a program key.
- **The API holds its port exclusively.** Before, another program could bind the same port on Windows and receive Grain's requests and keys, and "port taken, move to the next one" never happened.
- **"Yes" must be the whole reply.** "Okay, open notepad" used to approve whatever was waiting for confirmation; now it's a new request.
- **Pending confirmations are locked** while the API and tray list them, so they no longer race the engine.
- **Safer defaults for tool files.** `open` tools whose spoken words fill in a file or program path, and `keys` tools that type spoken text, now default to `confirm`.
- **Stricter API input.** Request bodies over 1 MB, bodies that aren't JSON objects, and non-numeric `limit`, `after` or `timeout` values are refused with 4xx errors instead of failing with 500s.
- **The update address must be `https://`**, or `http://` on this PC.

## 1.0.0

First distributable release.

- **Installer:** a per-user installer that needs no admin rights. It adds a Start-menu entry, can add a desktop shortcut and start with Windows, updates in place, and appears in Installed apps with a proper uninstaller that keeps your data unless asked.
- **Your data in `%APPDATA%\Grain`:** settings, history, notes, the log, and your tools, plugins and fine-tuned models live there. Grain 0.x files kept beside the exe are moved there automatically.
- **Fully offline:** the models are bundled, the Needle library's download check is switched off, and the network libraries aren't in the build at all.
- **Settings page** in the tool editor. The hotkey (press the keys you want), microphone with a live test, wake words, AI confidence, start with Windows, update checks, API port and a new access key. Changes apply without a restart, except the port.
- **First-run setup** for the microphone, hotkey, wake word and startup.
- **Any installed program opens by voice:** "open X" finds it in your Start menu without a bundled product list. User-defined aliases are supported through `app_aliases`.
- **One copy at a time:** starting Grain again opens the running copy's tool editor. If the API port is taken, Grain moves to the next free one.
- **Update checks:** off by default; when turned on, Grain checks an update address you set.
- **Smaller fixes:** the log rotates (about 3 MB at most), settings are saved atomically, there's an About item in the tray, and a version number throughout.

## 0.x

Development versions: tool registry, phrases, profiles, plugins, local API, tool editor and guide.
