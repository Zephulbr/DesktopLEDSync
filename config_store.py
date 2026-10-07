"""Shared config.json handling used by both the GUI and the core engine."""
import json
import os
import sys
import tempfile
import time

KEYRING_SERVICE = "DesktopLEDSync"
KEYRING_PLACEHOLDER = "USE_KEYRING"
DEFAULT_IDLE_COLOR = (255, 200, 100)
IDLE_BEHAVIORS = ("Default Color", "Turn Off", "Do Nothing")
DEFAULT_TRANSITION_SECONDS = 1.0
MAX_TRANSITION_SECONDS = 5.0

# Older configs stored idle behavior in snake_case
_IDLE_BEHAVIOR_ALIASES = {"default_color": "Default Color", "turn_off": "Turn Off", "do_nothing": "Do Nothing"}

# Safely determine the config path whether running from terminal or PyInstaller .exe
if getattr(sys, 'frozen', False):
    application_path = os.path.dirname(sys.executable)
else:
    application_path = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(application_path, "config.json")


class ConfigError(Exception):
    """Raised when config.json is missing or cannot be parsed."""


def read_config():
    """Read config.json, raising ConfigError if it is missing or unreadable."""
    if not os.path.exists(CONFIG_PATH):
        raise ConfigError(f"Could not find {CONFIG_PATH}. Please configure your smart lights first.")
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise ConfigError(f"Could not read {CONFIG_PATH}: {e}") from e


def write_config(config):
    """Write config.json atomically so the engine never reads a half-written file."""
    fd, tmp_path = tempfile.mkstemp(dir=application_path, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        # On Windows the replace fails if another thread has the file open at that instant, so retry briefly
        for attempt in range(10):
            try:
                os.replace(tmp_path, CONFIG_PATH)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def parse_rgb(value):
    """Parse [r, g, b] or "r,g,b" into a tuple of three 0-255 ints. Returns None if invalid."""
    if isinstance(value, str):
        parts = value.replace(" ", "").split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return None
    if len(parts) != 3:
        return None
    try:
        rgb = tuple(int(p) for p in parts)
    except (TypeError, ValueError):
        return None
    if not all(0 <= c <= 255 for c in rgb):
        return None
    return rgb


def normalize_idle_behavior(value):
    value = _IDLE_BEHAVIOR_ALIASES.get(value, value)
    return value if value in IDLE_BEHAVIORS else "Do Nothing"


def parse_transition_seconds(value):
    """Parse the color fade duration, falling back to the default when it is missing or invalid."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TRANSITION_SECONDS
    if seconds != seconds:  # NaN
        return DEFAULT_TRANSITION_SECONDS
    return min(max(seconds, 0.0), MAX_TRANSITION_SECONDS)


def resolve_password(credentials):
    """Return the real password, fetching it from Windows Credential Manager when needed."""
    username = credentials.get("username", "")
    password = credentials.get("password", "")
    if password == KEYRING_PLACEHOLDER:
        if not username:
            return None
        import keyring
        return keyring.get_password(KEYRING_SERVICE, username)
    return password


def migrate_plaintext_password(config):
    """Move a plaintext password from config.json into the keyring. Returns True if it migrated one."""
    creds = config.get("credentials") or {}
    username = creds.get("username", "")
    password = creds.get("password", "")
    if not username or not password or password == KEYRING_PLACEHOLDER:
        return False

    import keyring
    keyring.set_password(KEYRING_SERVICE, username, password)
    creds["password"] = KEYRING_PLACEHOLDER
    write_config(config)
    return True
