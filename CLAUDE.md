# Desktop LED Sync

## Versioning

- The app version lives in `version.py` (`__version__`). It is shown in the window title, header, tray tooltip, log and the .exe's file properties.
- **Bump `__version__` in every change that touches the app** (patch for fixes, minor for features), so the user can tell whether a build is up to date.
- A push to `main` with a version that has no release yet builds the .exe and publishes it as release `v<version>` automatically. Without a bump, nothing is released, so the latest release would silently lag behind `main`.
- Manually pushed tags and Actions-tab runs must match `version.py`, or the release workflow fails.
