# Releasing Grain

Before a public release:

- **Code signing:** set `GRAIN_SIGN_THUMBPRINT` to your code-signing certificate's SHA-1 thumbprint, and have `signtool.exe` (Windows SDK) on PATH. The build then signs the app, the uninstaller and the installer. Unsigned builds work, but SmartScreen warns people before running them.
- **Licences:** the Needle and Whistle models are Apache-2.0 (Cactus Compute), so redistribution is allowed with the notices, which the build includes. pystray is LGPL-3.0, so it ships as plain, replaceable source in `_internal\pystray`, not compiled in. Everything else bundled is MIT, BSD, Apache or PSF. Grain itself is MIT-licensed ([LICENSE](LICENSE)), and the build ships it as `LICENSE.txt`.
- **Version:** bump `__version__` in [grain/__init__.py](grain/__init__.py) and add a [CHANGELOG.md](CHANGELOG.md) entry. If the settings format changes, bump `CONFIG_VERSION` in [grain/config.py](grain/config.py) and add a step to `MIGRATIONS`.

The build environment needs both runtime and Windows build dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build-windows.txt
```

See [SECURITY.md](SECURITY.md) before publishing a release.
