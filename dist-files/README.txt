GRAIN - an offline voice assistant for Windows
==============================================

Grain listens when you press a hotkey (Ctrl+Alt+Space unless you change it), turns your speech
into text, and does what you asked: open programs, search the web or YouTube, control music and
volume, set timers, take notes, and anything you teach it. Speech recognition and the AI both run
on this PC. Nothing you say leaves it, and Grain makes no internet connections unless you turn on
update checks in Settings.

Getting started
  - Grain lives in the system tray, by the clock (click ^ if you can't see it).
  - The first time it starts, a setup page opens in your browser: pick your microphone, test it,
    and choose your hotkey.
  - Then press the hotkey and say "open notepad", "what time is it" or "search YouTube for cats".
  - Right-click the tray icon for the tool editor, settings, profiles and the full guide.

Where things are
  - The app:            this folder
  - Your data:          %APPDATA%\Grain  (settings, history, notes, your tools and plugins)
  - Uninstall:          Windows Settings > Apps > Installed apps > Grain, or "Uninstall Grain.exe" here.
                        Your data is kept unless you tick "also delete my settings".

Good to know
  - The hotkey doesn't work while a window running as administrator is in front. That's a Windows
    security rule for all non-admin programs, not a fault.
  - In wake-word mode, speech recognition sometimes hears "Thank you." in background noise.
    It's harmless (it doesn't start with the wake word, so Grain ignores it), and we've decided
    it's funny enough to keep.

Licences
  Grain is free software under the MIT License (LICENSE.txt). It includes third-party software,
  including the Needle and Whistle models by Cactus Compute (Apache-2.0). See THIRD_PARTY_NOTICES.txt
  and the licenses folder.
