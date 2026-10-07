"""Picking which app's media session to follow when several are open (e.g. Spotify and a YouTube tab)."""
import re

ANY_APP = "Any app"

# Media apps offered in the "Preferred app" list even when they aren't open right now
COMMON_APPS = ("Spotify", "Apple Music", "TIDAL", "Deezer", "Amazon Music", "Media Player", "VLC",
               "Chrome", "Edge", "Firefox", "Brave", "Opera")

# Windows identifies a session by the app's AppUserModelID, e.g. "Spotify.exe",
# "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify" or "308046B0AF4A39CB" (Firefox).
# Matched against the lowercased ID, first match wins.
_KNOWN_APPS = (
    ("spotify", "Spotify"),
    ("applemusic", "Apple Music"),
    ("itunes", "iTunes"),
    ("tidal", "TIDAL"),
    ("deezer", "Deezer"),
    ("amazonmusic", "Amazon Music"),
    ("zunemusic", "Media Player"),
    ("zunevideo", "Movies & TV"),
    ("vlc", "VLC"),
    ("foobar2000", "foobar2000"),
    ("musicbee", "MusicBee"),
    ("aimp", "AIMP"),
    ("msedge", "Edge"),
    ("microsoftedge", "Edge"),
    ("chrome", "Chrome"),
    ("firefox", "Firefox"),
    ("308046b0af4a39cb", "Firefox"),
    ("6f193ccc56814779", "Firefox"),  # Firefox Nightly
    ("brave", "Brave"),
    ("opera", "Opera"),
    ("vivaldi", "Vivaldi"),
)


def app_name(app_id):
    """A readable name for a media session's AppUserModelID."""
    lowered = (app_id or "").lower()
    for needle, name in _KNOWN_APPS:
        if needle in lowered:
            return name
    # Unknown app: "Publisher.App_hash!Entry" -> "Entry", "Something.exe" -> "Something"
    name = (app_id or "").split("!")[-1]
    name = re.sub(r"\.exe$", "", name, flags=re.IGNORECASE)
    name = name.split(".")[-1].split("\\")[-1]
    return name or "Unknown app"


def _is_playing(session, playing_status):
    try:
        return session.get_playback_info().playback_status == playing_status
    except Exception:
        return False


def pick_session(manager, preferred, only_preferred, playing_status):
    """
    Choose the session to follow. With a preferred app, it wins whenever it is playing; otherwise
    another playing app is followed, unless only_preferred is set. Without a preference, Windows'
    own choice (the one in the media flyout) is used.
    """
    current = manager.get_current_session()
    if not preferred or preferred == ANY_APP:
        return current

    sessions = list(manager.get_sessions())
    wanted = preferred.lower()
    matches = [s for s in sessions if app_name(s.source_app_user_model_id).lower() == wanted]
    for session in matches:
        if _is_playing(session, playing_status):
            return session
    if only_preferred:
        return matches[0] if matches else None
    if current is not None and _is_playing(current, playing_status):
        return current
    for session in sessions:
        if _is_playing(session, playing_status):
            return session
    return current


def choices(open_app_names, saved):
    """The values for the "Preferred app" list: any app, the open apps, the common ones, and the saved one."""
    names = [ANY_APP]
    for name in [*sorted(open_app_names, key=str.lower), *COMMON_APPS, saved]:
        if name and name.lower() not in (n.lower() for n in names):
            names.append(name)
    return names
