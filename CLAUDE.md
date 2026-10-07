# Desktop LED Sync

## Versioning

- The app version lives in `version.py` (`__version__`). It is shown in the window title, header, tray tooltip, log and the .exe's file properties.
- **Bump `__version__` in every change that touches the app** (patch for fixes, minor for features), so the user can tell whether a build is up to date.
- Releases are tagged `v<version>`; the release workflow fails if the tag doesn't match `version.py`.
