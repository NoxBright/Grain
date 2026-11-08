# Grain App

An offline Windows voice assistant. Whistle turns speech into text, Needle picks a tool to call, and Grain runs it. Everything runs locally on the CPU.

Install with `Grain Setup <version>.exe`, then press **Ctrl+Alt+Space** and speak. The first run opens a short setup for the microphone and hotkey. The tray menu has the tool editor, settings, profiles, microphone choice and wake-word mode.

| Where | What |
|---|---|
| `%LOCALAPPDATA%\Programs\Grain` | The installed app, models, default tools, licences and uninstaller. Never written to while running. |
| `%APPDATA%\Grain` | The user's data: `settings.json`, `history.jsonl`, `notes.md`, `grain.log`, and their own `tools\`, `plugins\` and `models\`. Set `GRAIN_DATA_DIR` to use another folder, as the tests do. |

Grain makes no network connections. The models are bundled, the needle package's download ping is disabled (see [grain/offline.py](grain/offline.py)), and the network libraries aren't in the build at all. The only exception is the optional update check, which is off by default.

The tool editor (tray → Tool editor) has a **Guide** tab with the full user documentation: setup, troubleshooting, making tools, plugins, profiles, fine-tuning and the API. It fills in this machine's port, hotkey and wake words.

## How a request flows

```
mic / hotkey / API text ─→ Whistle ─→ split on "then" ─→ phrase match (all tools)
   └ no match ─→ Needle (active profile's tools) ─→ argument check ─→ confidence + grounding check
─→ safety check ─→ run the tool ─→ notification, history, API events
```

- **Phrases** are checked against every tool, whatever the profile, since they're exact.
- **Profiles** only decide which tools Needle guesses between when no phrase matched. The base model gets less accurate past about 10 tools, so Grain ships five small ones:

  | Profile | Used when | Tools Needle guesses between |
  |---|---|---|
  | `default` | Anything else | Open apps and programs, search, YouTube, media, volume, time, timer, notes, screenshot |
  | `gaming` | Picked from the tray or by your own rule | Programs, media, volume, screenshot, dice, timer, time |
  | `coding` | Picked from the tray or by your own rule | Programs, Windows apps, search, notes, timer, screenshot, time |
  | `media` | Picked from the tray or by your own rule | YouTube, media, volume, search, programs |
  | `system` | Picked from the tray | Windows apps, lock, battery, volume, time, screenshot, timer |

- **Argument check:** Needle's guesses are checked against the tool's parameters before anything runs.
  - Invented arguments are dropped.
  - Values are converted ("twenty five" becomes 25, "UP" becomes `up`).
  - Unknown tools, missing required values and impossible values are refused with a "Couldn't run that" message.
- **Grounding:** if none of the chosen tool's own words (its name, keywords, allowed values, phrase words) appear in the request, the guess needs 0.8 confidence. This stops confident nonsense such as "fire up steam" turning into media controls.
- **Safety levels:**
  - `run` runs the tool straight away.
  - `confirm` waits for a spoken "yes" (the whole reply: "okay, open notepad" is a new request, not a yes), the tray's Confirm item, or the tool editor. Programs using a program key can't confirm.
  - `strict` needs at least 0.8 confidence.
- **Confidence thresholds:** the default is `confidence_threshold`. A tool can set its own `threshold`, and `tool_thresholds` in the config overrides both.

## Phrases: exact commands that skip Needle

Needle is small and unreliable at copying free text, like a search query or a note, out of a short command. Any tool can list **phrases**, which are checked before Needle:

```json
"phrases": [
  "search youtube for {query}",
  "play {query} on youtube",
  {"say": "next song", "arguments": {"action": "next"}}
]
```

- **Whole utterance:** a phrase only matches if it covers everything you said. Case and punctuation are ignored, and the longest matching phrase wins.
- **Placeholders:** `{param}` takes the words said in that spot.
  - Free-text values keep your original wording, like "lo-fi" or "C++ 20".
  - Number words fill number parameters ("two" becomes 2).
  - Enum parameters only accept one of their allowed values.
- **Fixed arguments:** the `{"say", "arguments"}` form sets arguments for a phrase with no placeholders.
- **Still checked:** phrase matches count as fully confident, but a tool's safety level still applies, so `confirm` tools still ask first.
- **Fallback:** anything that doesn't match a phrase goes to Needle as before.

Built-in tools, the example tool files and the dice plugin all come with phrases. In the tool editor there's a Phrases box, with one phrase per line.

## Making tools

There are four kinds, all in one registry. Tool names must be unique and in snake_case.

### 1. No-code tool files (`tools\*.json`)

Make them in the tool editor (tray → Tool editor) or by hand. Changes load within a couple of seconds.

```json
{
  "name": "open_youtube",
  "description": "Open YouTube, searching for a video if one is named.",
  "parameters": {"query": {"type": "string", "required": false}},
  "action": {"type": "open", "target": "https://www.youtube.com/results?search_query={query}"},
  "safety": "run",
  "keywords": ["youtube"]
}
```

| Action | Fields | Notes |
|---|---|---|
| `open` | `target` | URL, file, folder or program. URL values are encoded. Defaults to `confirm` when what's said fills in a file or program path (a target that isn't a URL but has a `{placeholder}`). |
| `run` | `program`, `args[]`, `cwd` | Started directly, never through a shell. Defaults to `confirm`. |
| `keys` | `keys`, `text` | Presses a shortcut like `windows+shift+s`, and/or types text. Defaults to `confirm` when `text` has a `{placeholder}`, since it types into whatever window is in front. |
| `powershell` | `script`, `timeout` | Arguments arrive as `$env:GRAIN_<name>`, never pasted into the script. Defaults to `confirm`. |
| `http` | `method`, `url`, `headers`, `body` | In `body`, a value of exactly `"{x}"` keeps its type. |

`{param}` placeholders are filled from Needle's arguments. Parameters use the short form shown above (`type`, `description`, `enum`, `required`), or a full JSON schema.

### 2. Python plugins (`plugins\*.py`)

```python
from typing import Literal
from grain import tool

@tool(safety="confirm", keywords=["service"])
def restart_service(name: Literal["web", "db"]):
    "Restart one of the local services."
    ...
    return "restarted " + name
```

The docstring's first line is the description Needle reads, and the type hints become the parameters. Inside the exe, plugins can use the standard library and the packages Grain ships, but nothing installed separately.

### 3. Remote tools (over the API)

Another program on this PC registers tools along with a callback URL, using a program key made for it in the tool editor (Settings → Program keys). When Needle picks one, Grain sends `POST {"tool", "arguments"}` to the callback, and the program can reply `{"result": "text"}`. Callbacks must be on `127.0.0.1` or `localhost`. See [examples/remote_tool_client.py](examples/remote_tool_client.py).

### 4. Built-in tools

The built-ins in [grain/builtin.py](grain/builtin.py) are general Windows commands only:

| Tool | What it does |
|---|---|
| `open_app` | Opens Windows apps (Notepad, Calculator, File Explorer, Task Manager, Settings, the browser), listed under `apps` in `settings.json` |
| `adjust_volume` | Volume up, down or mute |
| `media_control` | Play/pause, next, previous, stop |
| `tell_time` | Shows the time and date |
| `lock_computer` | Locks the screen |

Everything else is a plugin or tool file, so you can edit or delete it:

| File | Tools |
|---|---|
| [plugins/apps.py](plugins/apps.py) | `open_program`: finds installed programs by name or user-defined alias |
| [plugins/everyday.py](plugins/everyday.py) | `search_web`, `set_timer`, `take_note` |
| [plugins/dice.py](plugins/dice.py) | `roll_dice` |
| `tools\*.json` | `open_youtube`, `take_screenshot`, `battery_level` |

### Opening programs

[plugins/apps.py](plugins/apps.py) opens **any program in the user's Start menu** by name. No product list is bundled and no association with a program maker is implied.
- **Matching:** names are matched in order:
  - exact name
  - the name without spaces
  - a name it starts with
  - a name containing all its words
  - a close spelling
- **Skipped entries:** uninstallers, help pages and website links in the Start menu are ignored.
- **Aliases:** add entries to `app_aliases` in `settings.json` to map other ways of saying a name to an installed Start-menu name. They only count if the app is installed.
- **Falling through:** the "open {app}" phrase has a `check`. When `{app}` isn't a real program, the phrase doesn't claim the request, so "start a timer" still reaches the timer.
- **In your own plugins,** `from grain import open_target` opens any program, file, URL or `start:<name>`. A `check=` on `@tool` does the same fall-through for your own catch-all phrases.

## Local API

The API is at `http://127.0.0.1:8765` (`api_port`). It only accepts connections from this PC, and requests from other hosts or web pages are refused. Grain holds its port exclusively, so no other program can listen on it alongside Grain; if the port is taken when Grain starts, it moves to the next free one and saves it. Request bodies are limited to 1 MB.

Every `/v1` call needs `Authorization: Bearer <key>`, with one of two kinds of key:

- **The tool editor key** (`api_token` in `%APPDATA%\Grain\settings.json`) is the tool editor's own. It can do everything except register a program's tools. Don't give it to programs.
- **Program keys** are made in the tool editor (Settings → Program keys), one per program, named after it. The key is shown once; `settings.json` keeps only its SHA-256 fingerprint under `api_keys`. Revoking a key also removes that program's tools. Give it to the program, for example as `GRAIN_API_KEY`.

A program key can only use the routes marked **program** or **any**:

- `POST` and `DELETE /v1/tools` reach only that program's own tools.
- `GET /v1/events` returns only the results of its own requests.
- `GET /v1/status` returns a short form.
- Its requests are recorded with source `program:<name>`, and a "yes" in them never approves a pending call.

| Method and path | Key | What it does |
|---|---|---|
| `GET /v1/status` | any | Current profile, active tools, load errors, mic (a program gets its own tools and the profile) |
| `GET /v1/tools` | tool editor | Every tool, with its source, safety and whether it's active |
| `POST /v1/tools` | program | Register the program's remote tools: `{callback, tools: [{name, description, parameters, safety?, threshold?}]}` (`client`, if sent, must be the key's program) |
| `DELETE /v1/tools` | any | Remove the program's remote tools (the tool editor key passes `?client=x`) |
| `GET` / `PUT` / `DELETE /v1/tool_files/{name}` | tool editor | Read, write or delete a no-code tool file |
| `POST /v1/interpret` | any | `{text, execute=true, profile?}`, returns `results[]` with calls, confidence, outcome and message |
| `GET /v1/pending`, `POST /v1/pending/{id}/confirm` or `/cancel` | tool editor | Requests waiting for a yes |
| `GET /v1/history`, `POST /v1/history/{id}/correction` | tool editor | The log, and corrections: `{calls: [...]}`, or `[]` for "no tool" |
| `GET /v1/training_data` | tool editor | Corrected entries as JSONL in the Needle platform's chat format |
| `GET` / `PUT /v1/profiles` | tool editor | `{profiles, profile_rules, active_profile}` |
| `GET /v1/events?after=N&timeout=25` | any | Long-poll for new results (a program sees only its own) |
| `GET /v1/keys`, `POST /v1/keys`, `DELETE /v1/keys/{id}` | tool editor | List, make (`{client}`; the key is in the reply, once) or revoke program keys |
| `GET` / `PUT /v1/settings` | tool editor | The settings the Settings tab edits; changes are validated, then applied live (`api_port` needs a restart). `update_url` must be `https://` |
| `POST /v1/settings/test_microphone` | tool editor | `{device?}`: listens for 3 s and returns the level and a verdict |
| `POST /v1/settings/regenerate_token` | tool editor | Makes a new tool editor key; the old one stops working immediately. Program keys are unaffected |
| `POST /v1/setup/done` | tool editor | Marks the first-run setup as finished |
| `POST /v1/open/{data,tools,plugins,models,log,notes}` | tool editor | Opens that folder or file on this PC |
| `POST /v1/updates/check` | tool editor | Checks `update_url`, only when asked |

## Improving accuracy

Use the History tab to answer 👍 or 👎 for each command, then **Download training data**. The file is ready to use with `needle finetune` locally (free; needs `pip install "cactus-needle[train]"`), or to upload to the Cactus platform.

```bash
needle finetune grain-training.jsonl --epochs 3 --out my-adapter.safetensors
```

```bash
needle build --lora my-adapter.safetensors --out models\needle3-mine.cact
```

Put the `.cact` file in `%APPDATA%\Grain\models`, then set **Model file** in Settings to `models\needle3-mine.cact`. It takes effect on the next command. Locally trained adapters don't report confidence, so Grain runs every call the model makes. Set risky tools to `confirm` first.

## Supported platform

The first public release supports Windows. The source contains additional experimental platform
work, but no non-Windows packaged release is provided or supported yet.

## Tests

| File | What it covers |
|---|---|
| [tests/test_tools.py](tests/test_tools.py) | Every tool through its phrases, opening programs from a fake Start menu, argument edge cases, confirmations, profiles, grounding and the Needle fallback |
| [tests/test_app.py](tests/test_app.py) | Default tools copied once (deleted ones stay deleted), settings upgrades, moving 0.x files, atomic saves, settings validation, the settings API, update checks, single instance |
| [tests/test_offline.py](tests/test_offline.py) | Loads both models, makes an AI guess and transcribes speech while every non-local connection is blocked and recorded; fails if Grain tries to go online |

Windows actions (opening programs, key presses, locking, timers, the browser, the startup registry entry) are intercepted, so nothing happens on screen. The tests use a temporary data folder, never the real `%APPDATA%\Grain`.

```bash
.venv\Scripts\python -m unittest discover tests -v
```

## Building a release

```bash
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

For the maintainer release checklist, see [RELEASING.md](RELEASING.md).

This produces:
- **`dist\Grain\`:** the app folder. A PyInstaller folder build, which is less likely to trip antivirus than a single self-unpacking exe. It holds the models, the default tools and plugins (copied into each user's data folder on first run), `THIRD_PARTY_NOTICES.txt` with a `licenses\` folder, a user `README.txt`, the changelog and `Uninstall Grain.exe`.
- **`dist\Grain Setup <version>.exe`:** the per-user installer ([installer/grain_setup.py](installer/grain_setup.py)).
  - It needs no admin rights, adds a Start-menu entry, and can add a desktop shortcut and start with Windows.
  - It updates in place, keeping the user's data.
  - It's listed in Installed apps.
  - Silent use: `--silent [--dir PATH] [--desktop] [--startup] [--no-launch]`. Uninstall with `"Uninstall Grain.exe" --silent [--remove-data]`. Silent mode never shows a dialog: failures go to `%TEMP%\grain-setup.log` and exit code 1.
  - It refuses to install into, or uninstall from, a folder that isn't a Grain install.

The build environment needs both runtime and Windows build dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build-windows.txt
```

See [SECURITY.md](SECURITY.md) before publishing a release.

Dev-only scripts and test recordings are in `dev\` and never ship; `dev\licenses-src\` is a
release-build input for the model licence notices. `start_assistant.bat` runs Grain from source.
