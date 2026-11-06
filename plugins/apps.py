"""Opens any installed program by voice: "open Steam", "launch Discord", "start VS Code".

Programs are found in your Start menu, the same list Windows Start shows, so anything you
install works straight away. Grain reloads this file as soon as you save it.

NICKNAMES maps other things you might say (or that speech recognition might write, e.g.
"G.O.G." arrives as "g o g") to a Start-menu name. They're only used if that app is installed.
To see exact Start-menu names, run Get-StartApps in PowerShell.
"""
from grain import open_target, tool
from grain.launch import find_start_app

NICKNAMES = {
    "gog": "GOG GALAXY", "g o g": "GOG GALAXY", "galaxy": "GOG GALAXY",
    "vs code": "Visual Studio Code", "vscode": "Visual Studio Code", "code": "Visual Studio Code",
    "chat gpt": "ChatGPT", "chat g p t": "ChatGPT",
    "word": "Word", "excel": "Excel", "powerpoint": "PowerPoint", "outlook": "Outlook",
    "edge": "Microsoft Edge", "chrome": "Google Chrome", "firefox": "Firefox",
    "terminal": "Terminal", "command prompt": "Command Prompt", "paint": "Paint",
}

VERBS = ["open", "open up", "open the", "launch", "start", "start up", "run"]


def is_installed(arguments):
    """Only claim "open {app}" when {app} is a real program, so "start a timer" goes elsewhere."""
    return find_start_app(arguments.get("app", ""), NICKNAMES) is not None


@tool(keywords=["program", "app", "launch"] + sorted({w for n in NICKNAMES for w in n.split() if len(w) > 2}),
      phrases=[f"{verb} {{app}}" for verb in VERBS], check=is_installed)
def open_program(app: str):
    "Open any installed program by name, such as Steam, Discord or Visual Studio Code."
    name = find_start_app(app, NICKNAMES)
    if name is None:
        return f"Couldn't find a program called '{app}' in the Start menu"
    open_target("start:" + name)
    return f"opened {name}"
