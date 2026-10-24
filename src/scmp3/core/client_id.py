"""The SoundCloud client_id override in Settings: checking one, and handing it to yt-dlp.

yt-dlp keeps the client_id it uses in its cache, and finds a new one when SoundCloud rejects it.
So the override is written there when it's saved and again at every start (TECH_SPEC §14).
"""

import re

from scmp3.core import engine, settings
from scmp3.core.settings import SettingsStore

# The setting that holds the override.
SETTING = "soundcloud_client_id"
# Why a typed client_id wasn't saved. web/strings/settings.js has the text.
NOT_A_CLIENT_ID = "NOT_A_CLIENT_ID"
# A SoundCloud client_id, as yt-dlp finds it in SoundCloud's own scripts.
CLIENT_ID_PATTERN = re.compile(r"[0-9A-Za-z]{32}")


def client_id_problem(value: str | None) -> dict[str, str] | None:
    """What's wrong with a client_id override. None leaves yt-dlp to find its own."""
    if value is None or CLIENT_ID_PATTERN.fullmatch(value):
        return None
    return {"reason": NOT_A_CLIENT_ID}


def apply_saved_override(store: SettingsStore | None = None) -> None:
    """Write the saved override into yt-dlp's cache again, in case yt-dlp replaced it.

    This imports yt-dlp at start-up, so it does nothing when no override is set.
    """
    client_id = (store or SettingsStore(settings.default_path())).load().soundcloud_client_id
    if client_id is not None:
        engine.store_client_id(client_id)
