# Builds "Grain App.exe" into this folder. It reads models\, tools\, plugins\ and
# assistant_config.json beside itself, so keep it here or copy those along.
# PyInstaller's working files (full of absolute paths) go to a temp folder that is removed afterwards.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = ".\.venv\Scripts\python.exe"
$work = Join-Path $env:TEMP "grain-build"

# Bundle the Needle engine DLL so the exe never has to download it.
$engine = & $py -c "import os, needle.agent.fetch as f; print(os.path.join(f.cache_dir(3), 'libneedle.dll'))"
if (-not (Test-Path $engine)) { throw "Needle engine not cached at $engine; run smoke_test.py once first." }

try {
    & $py -m PyInstaller --noconfirm --onefile --windowed `
        --name "Grain App" `
        --icon "$PSScriptRoot\assets\grain.ico" `
        --add-data "$PSScriptRoot\assets\grain.png;assets" `
        --add-data "$PSScriptRoot\grain\ui;grain\ui" `
        --add-binary "$engine;." `
        --collect-all needle `
        --collect-data _sounddevice_data `
        --hidden-import pystray._win32 `
        --distpath . --workpath $work --specpath $work `
        grain_app.py
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
