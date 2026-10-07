import asyncio
import hashlib
import os
import sys
from io import BytesIO

from config_store import (
    CONFIG_PATH, DEFAULT_IDLE_COLOR, ConfigError,
    migrate_plaintext_password, normalize_idle_behavior, parse_rgb, parse_transition_seconds, read_config,
)

# Set by gui.py so log messages reach the GUI log panel
log_queue = None

def log(level, message):
    """Post a log message to the GUI queue, or fall back to print."""
    print(message)
    if log_queue is not None:
        log_queue.put((level, message))

from colorthief import ColorThief

# Light Providers
from providers.tapo import TapoProvider
from providers.wled import WLEDProvider

# Windows Runtime APIs (winrt is the maintained successor of winsdk; both expose the same API)
try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus
    )
    from winrt.windows.storage.streams import DataReader
except ImportError:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as MediaManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus
    )
    from winsdk.windows.storage.streams import DataReader

DEFAULT_POLL_INTERVAL = 1.5
CONNECT_TIMEOUT_SECONDS = 20
# Windows often updates the track title before the thumbnail, so a missing or unchanged
# thumbnail is re-checked for this many polls before we accept it
MAX_ART_ATTEMPTS = 3
# How often a fade sends an in-between color to lights that can't fade by themselves
FADE_STEP_SECONDS = 0.1

# --- Media Extraction Logic ---
async def get_media_session():
    """Gets the active Windows Media session (Spotify, Tidal, Web, etc)"""
    session_manager = await MediaManager.request_async()
    return session_manager.get_current_session()

async def read_thumbnail_bytes(thumbnail_ref):
    """Reads the album art Windows Runtime stream into a standard Python byte string"""
    if thumbnail_ref is None:
        return None

    stream = None
    reader = None
    try:
        stream = await thumbnail_ref.open_read_async()
        size = stream.size
        if not size:
            return None

        # Read the stream using a DataReader
        reader = DataReader(stream.get_input_stream_at(0))
        await reader.load_async(size)

        # The Windows Runtime buffer requires a specific read pattern in python
        # We need to extract the bytes manually into a standard python format
        buffer = bytearray(size)
        reader.read_bytes(buffer)
        return bytes(buffer)
    except Exception as e:
        log("error", f"Error reading album art: {e}")
        return None
    finally:
        for closable in (reader, stream):
            if closable is not None:
                try:
                    closable.close()
                except Exception:
                    pass

def get_dominant_color(image_bytes):
    """Uses colorthief to find the most prominent vibrant color"""
    if not image_bytes:
        return None

    try:
        image_stream = BytesIO(image_bytes)
        color_thief = ColorThief(image_stream)

        # Pull 5 dominant colors and choose the first vibrant one
        palette = color_thief.get_palette(color_count=5)
        for color in palette:
            r, g, b = color
            # Simple saturation calculation as a vibrancy heuristic
            saturation = max(r, g, b) - min(r, g, b)
            if saturation > 50:
                return color

        # Fallback to absolute dominant if no vibrant colors are found
        return color_thief.get_color(quality=1)
    except Exception as e:
        log("error", f"Error extracting color: {e}")
        return None

# --- Provider Factory ---
def initialize_provider(config):
    provider_name = config.get("provider", "").lower()

    if provider_name == "tapo":
        return TapoProvider(config, log)
    elif provider_name == "wled":
        return WLEDProvider(config, log)
    else:
        raise ValueError(f"Unknown provider '{provider_name}' in config.json")

# --- Helpers ---
class LiveConfig:
    """Re-reads config.json only when it changes on disk, keeping the last good copy."""

    def __init__(self, initial):
        self.data = initial
        self._mtime = self._get_mtime()

    @staticmethod
    def _get_mtime():
        try:
            return os.stat(CONFIG_PATH).st_mtime_ns
        except OSError:
            return None

    def refresh(self):
        mtime = self._get_mtime()
        if mtime is not None and mtime != self._mtime:
            # Remember the mtime even on failure so a broken file is only reported once
            self._mtime = mtime
            try:
                self.data = read_config()
            except ConfigError as e:
                log("error", f"Ignoring config change: {e}")
        return self.data


class LightCommander:
    """
    Sends commands to the lights one at a time, in order. If several commands are queued
    while one is in flight, only the newest is kept, so the lights always end on the latest state.
    """

    def __init__(self):
        self._pending = None
        self._wakeup = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    def submit(self, command):
        """Queue a zero-argument coroutine function to run against the lights."""
        self._pending = command
        self._wakeup.set()

    def has_pending(self):
        """True when a newer command is waiting, so a long-running one should hand over."""
        return self._pending is not None

    async def _run(self):
        while True:
            await self._wakeup.wait()
            self._wakeup.clear()
            command, self._pending = self._pending, None
            if command is None:
                continue
            try:
                await command()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log("error", f"Failed to update lights: {e}")

    async def close(self):
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


class ColorFader:
    """
    Moves the lights to new colors with a smooth fade. Providers that can fade natively (WLED)
    are given the duration; for the rest, in-between colors are sent every FADE_STEP_SECONDS.
    """

    def __init__(self, provider, commander):
        self.provider = provider
        self.commander = commander
        self.current = None     # Color last sent to the lights, or None when off or unknown

    def set_color(self, color, match_brightness, duration):
        self.commander.submit(lambda: self._fade_to(tuple(color), match_brightness, duration))

    def turn_off(self):
        self.commander.submit(self._turn_off)

    async def _turn_off(self):
        self.current = None
        await self.provider.turn_off()

    async def _send(self, color, match_brightness, transition=0.0):
        await self.provider.set_color(color, match_brightness, transition)
        # (0, 0, 0) turns the lights off, so there is nothing to fade from afterwards
        self.current = color if any(color) else None

    async def _fade_to(self, target, match_brightness, duration):
        start = self.current
        if self.provider.supports_transitions:
            await self._send(target, match_brightness, duration)
            return
        if duration <= 0 or start is None or start == target or not any(target):
            await self._send(target, match_brightness)
            return

        loop = asyncio.get_running_loop()
        started = loop.time()
        while True:
            progress = min(1.0, (loop.time() - started) / duration)
            eased = progress * progress * (3 - 2 * progress)  # Ease in and out
            step = tuple(round(a + (b - a) * eased) for a, b in zip(start, target))
            if step != self.current:
                await self._send(step, match_brightness)
            if progress >= 1.0 or self.commander.has_pending():
                return  # Done, or a newer color carries on from here
            await asyncio.sleep(FADE_STEP_SECONDS)


async def sleep_unless_stopped(seconds, is_stopped):
    """Sleep for the given time, waking early if a stop is requested."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not is_stopped():
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(0.1, remaining))

# --- Main Event Loop ---
async def main(stop_event=None):
    """Run the sync engine until stop_event (a threading.Event) is set."""
    def is_stopped():
        return stop_event is not None and stop_event.is_set()

    log("info", "Desktop LED Sync - Initializing...")
    try:
        config = read_config()
    except ConfigError as e:
        log("error", str(e))
        return

    try:
        if migrate_plaintext_password(config):
            log("info", "Moved plaintext password into Windows Credential Manager.")
    except Exception as e:
        log("error", f"Failed to migrate password to Keyring: {e}")

    log("info", f"Provider: {config.get('provider')} @ {config.get('ip_address')}")

    # Initialize the specific brand of lights the user has
    try:
        provider = initialize_provider(config)
    except ValueError as e:
        log("error", str(e))
        return

    try:
        await asyncio.wait_for(provider.connect(), CONNECT_TIMEOUT_SECONDS)
        log("ok", f"Connected to {config.get('provider')} at {config.get('ip_address')}")
    except asyncio.TimeoutError:
        log("error", f"Failed to connect: no response from {config.get('ip_address')}")
        await provider.close()
        return
    except Exception as e:
        log("error", f"Failed to connect: {e}")
        await provider.close()
        return

    commander = LightCommander()
    try:
        if not is_stopped():
            await sync_loop(ColorFader(provider, commander), LiveConfig(config), is_stopped)
    finally:
        await commander.close()
        await provider.close()


async def sync_loop(fader, live_config, is_stopped):
    current_track = None        # (title, artist, album) currently shown on the lights
    art_pending = False         # True until album art has been applied for current_track
    art_attempts = 0
    last_art_hash = None        # Hash of the album art the current color came from
    last_color = None           # RGB value of the current song
    color_brightness = None     # match_brightness value last_color was sent with
    last_idle_key = None        # Tracks the idle settings that were last sent to the device
    last_error = None

    log("info", "Listening for media changes on Windows...")

    while not is_stopped():
        # Re-read settings each tick so GUI changes are picked up live
        settings = live_config.refresh().get("settings", {})
        match_brightness = bool(settings.get("match_brightness", False))
        transition = parse_transition_seconds(settings.get("transition_seconds"))
        try:
            poll_interval = max(0.2, float(settings.get("poll_interval_seconds", DEFAULT_POLL_INTERVAL)))
        except (TypeError, ValueError):
            poll_interval = DEFAULT_POLL_INTERVAL

        try:
            session = await get_media_session()
            status = session.get_playback_info().playback_status if session else None

            if status == PlaybackStatus.PLAYING:
                last_idle_key = None
                media_props = await session.try_get_media_properties_async()
                track = (media_props.title, media_props.artist, media_props.album_title)

                if track != current_track:
                    current_track = track
                    art_pending = True
                    art_attempts = 0
                    log("ok", f"Now Playing: {media_props.title} — {media_props.artist}")

                if art_pending:
                    art_attempts += 1
                    image_bytes = await read_thumbnail_bytes(media_props.thumbnail)
                    art_hash = hashlib.sha1(image_bytes).digest() if image_bytes else None
                    art_looks_stale = art_hash is None or art_hash == last_art_hash

                    # Otherwise wait a poll: the thumbnail may still be the previous track's, or not loaded yet
                    if not art_looks_stale or art_attempts >= MAX_ART_ATTEMPTS:
                        art_pending = False
                        if image_bytes is None:
                            log("info", "No album art for this track.")
                        else:
                            last_art_hash = art_hash
                            color = get_dominant_color(image_bytes)
                            if color:
                                last_color = color
                                color_brightness = match_brightness
                                log("ok", f"Color set: RGB{color}")
                                fader.set_color(color, match_brightness, transition)
                            else:
                                log("error", "Could not extract color from album art.")

                elif last_color is not None and match_brightness != color_brightness:
                    # 'Match Brightness' was toggled live in the GUI
                    color_brightness = match_brightness
                    log("ok", f"Color set: RGB{last_color} (Match Brightness changed)")
                    fader.set_color(last_color, match_brightness, transition)

            elif status != PlaybackStatus.CHANGING:
                # Paused, stopped, closed, or no media app at all
                behavior = normalize_idle_behavior(settings.get("idle_behavior", "Do Nothing"))
                idle_color = parse_rgb(settings.get("idle_color")) or DEFAULT_IDLE_COLOR
                current_idle_key = (behavior, idle_color, match_brightness)

                # Only send a command when the settings have actually changed
                if current_idle_key != last_idle_key:
                    last_idle_key = current_idle_key
                    # Re-apply the album art color when playback resumes
                    current_track = None
                    last_art_hash = None
                    art_pending = False
                    last_color = None
                    color_brightness = None
                    log("info", "Idle state — applying idle settings.")

                    if behavior == "Turn Off":
                        log("info", "Idle: Turning lights off.")
                        fader.turn_off()
                    elif behavior == "Default Color":
                        log("info", f"Idle: Default color RGB{idle_color}")
                        fader.set_color(idle_color, match_brightness, transition)
                    else:
                        log("info", "Idle: Keeping last color.")

            last_error = None
        except Exception as e:
            # Don't flood the log with the same failure every tick
            if str(e) != last_error:
                log("error", f"Media loop error: {e}")
                last_error = str(e)

        await sleep_unless_stopped(poll_interval, is_stopped)


def run(stop_event=None):
    """Run the engine on a fresh event loop in the current thread, blocking until it stops."""
    # Selector loop on Windows: the default Proactor loop misbehaves with the providers' network clients
    loop = asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main(stop_event))
        loop.run_until_complete(loop.shutdown_asyncgens())
    finally:
        asyncio.set_event_loop(None)
        loop.close()


if __name__ == "__main__":
    run()
