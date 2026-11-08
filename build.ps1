# Builds the release:
#   dist\Grain\               the app folder (Grain App.exe, models, default tools, licences)
#   dist\Grain Setup <v>.exe  the per-user installer, which also uninstalls
#
# Code signing: set GRAIN_SIGN_THUMBPRINT to your certificate's SHA-1 thumbprint (installed in your
# certificate store) and have signtool.exe from the Windows SDK available. Unsigned builds work, but
# Windows SmartScreen warns people before running them.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { (Get-Command python.exe).Source }
$work = Join-Path $env:TEMP "grain-build"
$version = & $py -c "import grain; print(grain.__version__)"
$app = "dist\Grain"
Write-Host "Building Grain $version"

# The Needle engine DLL, bundled so the app never downloads it. Prefer the pinned release input;
# the cache fallback is useful for local development with a matching cactus-needle package.
$vendoredEngine = Join-Path $PSScriptRoot "vendor\windows\libneedle.dll"
if (Test-Path $vendoredEngine) {
    $engine = $vendoredEngine
} else {
    $engine = & $py -c "import os, needle.agent.fetch as f; print(os.path.join(f.cache_dir(3), 'libneedle.dll'))"
}
if (-not (Test-Path $engine)) {
    throw "Needle native engine not found at $engine. Add vendor\windows\libneedle.dll or install a matching cactus-needle engine cache before building."
}

# Only the pinned model files may ship (see dev\fetch_models.py; run it without --check to fetch them).
& $py dev\fetch_models.py --check
if ($LASTEXITCODE -ne 0) { throw "The model files in models\ are missing or aren't the pinned versions. Run: python dev\fetch_models.py" }

Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue
if (Test-Path dist) {
    # Something still has files open (usually Grain App running from dist\Grain). Building over it would
    # nest the new app inside the old folder and ship both in the installer.
    throw "Couldn't empty dist (is Grain App running from dist\Grain? Quit it from the tray icon) and build again."
}
try {
    # huggingface_hub and its network stack are left out on purpose: Grain is offline, and anything that
    # tried to download would now fail loudly instead of reaching the internet. pystray is LGPL, so it's
    # shipped as plain source (below) instead of being compiled in.
    & $py -m PyInstaller --noconfirm --onedir --windowed --name "Grain App" --contents-directory _internal `
        --icon "$PSScriptRoot\assets\grain.ico" `
        --add-data "$PSScriptRoot\assets\grain.png;assets" `
        --add-data "$PSScriptRoot\grain\ui;grain\ui" `
        --add-binary "$engine;." `
        --collect-all needle `
        --collect-data _sounddevice_data `
        --exclude-module pystray --hidden-import six --hidden-import PIL.ImageDraw `
        --exclude-module huggingface_hub --exclude-module hf_xet --exclude-module httpx2 --exclude-module httpcore2 `
        --exclude-module fsspec --exclude-module tqdm --exclude-module jax --exclude-module flax --exclude-module optax `
        --exclude-module tkinter --exclude-module matplotlib `
        --distpath "$work\dist" --workpath "$work\work" --specpath "$work" `
        grain_app.py
    if ($LASTEXITCODE) { throw "PyInstaller failed" }
    New-Item -ItemType Directory -Force dist | Out-Null
    Move-Item "$work\dist\Grain App" $app

    # pystray as replaceable source (LGPL-3.0)
    $pystray = & $py -c "import os, pystray; print(os.path.dirname(pystray.__file__))"
    Copy-Item -Recurse $pystray "$app\_internal\pystray"
    Get-ChildItem "$app\_internal\pystray" -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force

    # Models, default tools and plugins (copied into each user's data folder on first run)
    New-Item -ItemType Directory -Force "$app\models", "$app\defaults\tools", "$app\defaults\plugins" | Out-Null
    Copy-Item models\needle3.cact, models\whistle.cact "$app\models\"
    Copy-Item tools\*.json "$app\defaults\tools\"
    Copy-Item plugins\*.py "$app\defaults\plugins\"

    # Licences and docs
    & $py dev\make_notices.py $app
    if ($LASTEXITCODE) { throw "generating third-party notices failed" }
    Copy-Item dist-files\README.txt, CHANGELOG.md "$app\"
    Copy-Item LICENSE "$app\LICENSE.txt"

    # The uninstaller: the installer script built without a payload
    Set-Content "$work\version.txt" $version -NoNewline
    & $py -m PyInstaller --noconfirm --onefile --windowed --name "Uninstall Grain" `
        --icon "$PSScriptRoot\assets\grain.ico" --add-data "$work\version.txt;." --add-data "$PSScriptRoot\assets\grain.png;." `
        --distpath $app --workpath "$work\uninstall-work" --specpath "$work" `
        installer\grain_setup.py
    if ($LASTEXITCODE) { throw "building the uninstaller failed" }

    # Sign, if a certificate is configured
    $toSign = @("$app\Grain App.exe", "$app\Uninstall Grain.exe")
    if ($env:GRAIN_SIGN_THUMBPRINT) {
        $signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if (-not $signtool) { throw "GRAIN_SIGN_THUMBPRINT is set but signtool.exe isn't on PATH (Windows SDK)" }
        & $signtool.Source sign /sha1 $env:GRAIN_SIGN_THUMBPRINT /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $toSign
        if ($LASTEXITCODE) { throw "signing the app failed" }
    } else {
        Write-Host "Not signed (set GRAIN_SIGN_THUMBPRINT to sign)." -ForegroundColor Yellow
    }

    # The installer carries the app folder as a zip
    Compress-Archive -Path "$app\*" -DestinationPath "$work\grain_payload.zip" -CompressionLevel Optimal
    & $py -m PyInstaller --noconfirm --onefile --windowed --name "Grain Setup $version" `
        --icon "$PSScriptRoot\assets\grain.ico" `
        --add-data "$work\grain_payload.zip;." --add-data "$work\version.txt;." `
        --add-data "$PSScriptRoot\assets\grain.png;." `
        --distpath dist --workpath "$work\setup-work" --specpath "$work" `
        installer\grain_setup.py
    if ($LASTEXITCODE) { throw "building the installer failed" }
    if ($env:GRAIN_SIGN_THUMBPRINT) {
        & $signtool.Source sign /sha1 $env:GRAIN_SIGN_THUMBPRINT /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "dist\Grain Setup $version.exe"
        if ($LASTEXITCODE) { throw "signing the installer failed" }
    }
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
$size = "{0:N0} MB" -f ((Get-ChildItem $app -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
Write-Host "Done: $app ($size) and dist\Grain Setup $version.exe"
