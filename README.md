# Desktop LED Sync

Desktop LED Sync is a standalone Windows application designed to automatically extract the dominant color from your currently playing music's album art (Tidal, Spotify, Apple Music) and push those colors to your smart LED strips in real-time.

Crucially, this application acts as a **standalone desktop client**. It sits entirely on your local network and communicates directly with your lights via your local Wi-Fi, making it a perfect lightweight solution for users who want dynamic lighting without needing to configure a full home automation server ecosystem.

## Core Features
- **Standalone Local Application:** Runs quietly in the background on your Windows PC, offering an alternative for users who don't have a Home Assistant instance running.
- **Universal Media Detection:** Automatically detects track changes across any app that uses standard Windows Media Controls (Tidal, Spotify, browsers, etc.).
- **Live Album Art Extraction:** Grabs the thumbnail of the currently playing song and runs it through a `colorthief` algorithm to find the dominant RGB color.
- **Provider Architecture:** Built with an extensible plugin system. Currently supports encrypted local **Tapo** connections and unauthenticated JSON **WLED** endpoints.
- **Custom Idle Behaviors:** Define exactly what your lights do when you pause the music (Turn Off, switch to a Default Color, or Do Nothing).
- **System Integration:** Completely native feel. Can be set to auto-start with Windows and seamlessly minimizes to the system tray.

## How it Works (The Architecture)

The application is split into three main layers to guarantee high performance and easy extensibility.

### 1. The Core Engine (`core.py` )
This is the heart of the application. It runs a lightweight, asynchronous loop in the background.
* It leverages the `winsdk` Python library to hook directly into the **Windows System Media Transport Controls (SMTC)**.
* When a song starts or changes, Windows hands the engine a direct memory stream of the album art thumbnail.
* The engine passes these bytes to `colorthief`, which calculates the most dominant `(R, G, B)` color values.
* It monitors live GUI toggles (like "Match Album Art Brightness") to calculate the final HSV values and hands them off to a loaded Provider.

### 2. The Modular Providers (`providers/`)
Because every smart light brand speaks a different language, the engine doesn't know *how* to talk to the lights. It just says "Set the color to Red." The Providers handle the translations:
* **Tapo (`providers/tapo.py`):** TP-Link Tapo lights require complex, local AES-128 encryption and session handshakes. This provider handles the secure login, decrypts the token, and translates the RGB color into the Hue/Saturation format Tapo expects.
* **WLED (`providers/wled.py`):** WLED controllers are entirely open. This provider simply constructs a lightweight JSON payload and fires it via an HTTP POST request to the strip's IP address.

*Because of this architecture, adding Philips Hue, Govee, or Nanoleaf support in the future simply requires dropping a new `.py` file into the `providers/` folder.*

### 3. The User Interface (`gui.py`)
A highly polished, dark-mode desktop interface built using `customtkinter`. 
* It completely eliminates the need for users to touch JSON configuration files.
* **Thread-Safe Log Panel:** Provides live colored terminal output directly in the app, showing real-time connectivity status, hex color codes, and errors across threads.
* **Native Touches:** Utilizes Segoe Fluent Windows icons for a premium OS-native look, custom color pickers, tooltips, and a fully functional right-click system tray menu (`pystray`). 
* **State Management:** When you toggle a setting like "Match Brightness," it saves to `config.json` instantly, and the Core Engine reads that live file so changes happen without restarting the app.

## Building the Executable

To build the application yourself into a portable executable, you will need Python installed. 

Install the required packages:
```bash
pip install -r requirements.txt
pip install pyinstaller
```

Run the build script:
```bash
python build.py
```

The standalone `.exe` will be found in the `dist` directory.
