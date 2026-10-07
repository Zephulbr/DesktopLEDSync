# Desktop LED Sync

Match your smart LED lights to the album art of whatever you're listening to on Windows.

Desktop LED Sync picks the dominant color from the current track's album art and sends it straight to your lights over your local Wi-Fi. It works with Spotify, Tidal, Apple Music, browsers, and any other app that shows up in the Windows media controls. There's no Home Assistant or other home-automation server to set up.

**Supported lights:** TP-Link **Tapo** and **WLED**

## Features

- **Works with any media app** that uses the standard Windows media controls
- **Preferred app** keeps the lights on, say, Spotify even when a YouTube video is also playing
- **Album art colors** pick the most vibrant color in the artwork, updated on every track change
- **Smooth color fades** blend the lights into each new color instead of jumping
- **Match brightness** (optional) dims the lights for darker album art
- **Idle behavior** controls what happens when you pause: switch to a default color, turn off, or keep the last color
- **Runs in the tray** and can start automatically with Windows, syncing in the background
- **Local and private:** talks to your lights directly on your network, and your Tapo password is stored in Windows Credential Manager, not in a file

## Download

1. Grab `DesktopLEDSync.exe` from the [latest release](https://github.com/Zephulbr/DesktopLEDSync/releases/latest).
2. Put it in its own folder. It saves its settings to `config.json` next to the `.exe`.
3. Run it. Windows SmartScreen may warn about an unrecognized app because the `.exe` isn't code-signed; choose **More info → Run anyway**.

Requires Windows 10 or 11.

## Setup

1. Choose your **Light brand** (`tapo` or `wled`).
2. Enter your light's **IP address**.
   - **Tapo:** Tapo app → your device → Settings → Device Info.
   - **WLED:** your router's list of connected devices, or the WLED app.
3. **Tapo only:** enter your Tapo account email and password (needed for local login).
4. Click **Save & apply**, then **Start syncing** and play some music.

### Settings

| Setting | What it does |
| --- | --- |
| Preferred app | When several apps are playing, follow this one. *Any app* follows whatever Windows shows in its media controls. Browsers count as one app (Chrome, Edge, Firefox...), whatever site is playing |
| Only follow the preferred app | Ignore every other app, even while the preferred one is paused or closed |
| When music pauses | **Default Color** switches to a color you pick, **Turn Off** powers the lights off, **Do Nothing** keeps the last album color |
| Idle color | The color used by *Default Color*, as `R,G,B` (0-255) or picked with the color chooser |
| When closing the window | Ask each time, minimize to the tray, or exit |
| Color fade | How many seconds the lights take to blend into a new color (0 switches instantly) |
| Match album art brightness | Dims the lights for darker artwork instead of always using full brightness |
| Mica background | Windows 11 only, off by default: tints the window with your wallpaper. Turn it back off if the window looks black |
| Start with Windows | Starts with Windows, hidden in the tray, and begins syncing right away |

IP address, provider and account changes take effect when you click **Save & apply**. Everything else applies instantly, even while syncing.

## Troubleshooting

- **"Failed to connect"**: check the IP address, and that the PC and lights are on the same network. Tapo bulbs can change IP after a router restart, so a reserved/static IP helps.
- **Tapo login errors**: double-check the email and password of the Tapo account the bulb is registered to.
- **Colors don't change**: make sure your player shows the track in the Windows media flyout (the volume popup). If it doesn't appear there, the app can't see it either.
- **The window looks black**: you've turned on **Mica background** and Windows can't draw it (transparency effects off, battery saver, Remote Desktop, high contrast, some graphics drivers and virtual machines). Turn it off again in the app's settings.
- **The lights follow the wrong app**: set **Preferred app** to the app you want them to follow, and turn on **Only follow the preferred app** to ignore everything else.
- **"Failed to save settings: Access is denied"**: something, usually antivirus or a cloud sync folder, is holding `config.json`. The app now falls back to overwriting the file in place, but if it still fails, move the `.exe` into a folder of its own outside Downloads.
- **"No album art for this track"**: the player isn't sharing artwork with Windows for that track. The lights keep their current color.

## Building from Source

You need Windows and Python 3.10 or newer.

```bash
pip install -r requirements.txt
python gui.py        # run directly
python build.py      # or build dist/DesktopLEDSync.exe
```

The app version is set in `version.py`, and shown in the window header and in the .exe's **Properties → Details**. Bump it with every change.

Releases are built by GitHub Actions on a Windows runner. Merging a change that bumps `version.py` into `main` publishes a release for that version automatically (tagged e.g. `v1.2.0`); pushes that keep the same version don't build anything. To build an .exe without releasing it, run **Build and Release** from the Actions tab with the version left blank.

## How It Works

| Part | Role |
| --- | --- |
| `core.py` | Background engine. Reads the current track and album art from the Windows media controls, extracts the color with `colorthief`, and sends it to the lights. |
| `providers/` | One file per light brand that turns "set this color" into the brand's own protocol: Tapo's encrypted local API or WLED's JSON API. |
| `gui.py` | The `customtkinter` settings window, live log, and tray icon. |
| `media_apps.py` | Names the media apps Windows reports and picks which one to follow. |
| `config_store.py` | Reads and writes `config.json`, and keeps the Tapo password in Windows Credential Manager. |

To add another brand (Hue, Govee, Nanoleaf...), add a `LightProvider` subclass in `providers/` implementing `connect`, `set_color` and `turn_off`, then register it in `initialize_provider` in `core.py` and in the provider dropdown in `gui.py`.
