# Desktop LED Sync

## Versioning

- The app version lives in `version.py` (`__version__`). It is shown in the window title, header, tray tooltip, log and the .exe's file properties.
- **Bump `__version__` in every change that touches the app** (patch for fixes, minor for features), so the user can tell whether a build is up to date.
- A push to `main` with a version that has no release yet builds the .exe and publishes it as release `v<version>` automatically. Without a bump, nothing is released, so the latest release would silently lag behind `main`.
- Manually pushed tags and Actions-tab runs must match `version.py`, or the release workflow fails.

## UI

- The Windows 11 look lives in `fluent.py`: the palette (`fluent.C`), Mica, and the `ComboBox` and `Toggle` controls. Use those instead of CustomTkinter's option menus, switches and sliders.
- With Mica in dark mode, DWM *adds* every painted color to the backdrop, so dark colors are stored as "desired color minus the backdrop" (`fluent.configure`). Take colors from `fluent.C` (or `fluent.exact()` for a color that must show as-is), never hard-coded hex values. Pure black (`#000000`) is see-through.
