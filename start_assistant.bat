@echo off
rem Starts Grain from source in the system tray without a console window.
rem (Or just run "Grain App.exe".)
start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0grain_app.py"
