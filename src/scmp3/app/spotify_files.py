"""Finding local-files.bnk, Spotify's list of local files, for the "songs not showing up" help.

Deleting it while Spotify is closed makes Spotify read the folders again (RESEARCH §2). Where it
lives depends on how Spotify was installed: the Windows installer, the Microsoft Store or macOS.
"""

import logging
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

LOCAL_FILES = "local-files.bnk"
# Spotify keeps one folder per account in its Users folder, named like "kaia-user".
USER_FOLDER_SUFFIX = "-user"
STORE_PACKAGES = "SpotifyAB.SpotifyMusic_*"

# Finds the Users folders Spotify may have, from the environment and the home folder.
UsersFinder = Callable[[Mapping[str, str], Path], list[Path]]

log = logging.getLogger(__name__)


def _windows_users_folders(env: Mapping[str, str], home: Path) -> list[Path]:
    """The installer version's Users folder, then the Microsoft Store version's."""
    folders: list[Path] = []
    roaming = env.get("APPDATA")
    if roaming:
        folders.append(Path(roaming) / "Spotify" / "Users")
    local = env.get("LOCALAPPDATA")
    if local:
        packages = sorted((Path(local) / "Packages").glob(STORE_PACKAGES))
        folders.extend(package / "LocalState" / "Spotify" / "Users" for package in packages)
    return folders


def _mac_users_folders(env: Mapping[str, str], home: Path) -> list[Path]:
    return [home / "Library" / "Application Support" / "Spotify" / "Users"]


def _linux_users_folders(env: Mapping[str, str], home: Path) -> list[Path]:
    config = env.get("XDG_CONFIG_HOME") or str(home / ".config")
    return [Path(config) / "spotify" / "Users"]


# Where each OS keeps Spotify's Users folders. Any other OS looks where Linux does.
USERS_FOLDERS: dict[str, UsersFinder] = {
    "win32": _windows_users_folders,
    "darwin": _mac_users_folders,
}


def local_files_location(
    *,
    platform: str = sys.platform,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path | None:
    """The newest local-files.bnk, or else Spotify's folder for an account, or None.

    None means Spotify has never run on this computer, or keeps its files somewhere unknown.
    """
    finder = USERS_FOLDERS.get(platform, _linux_users_folders)
    users_folders = finder(os.environ if env is None else env, home or Path.home())
    accounts = [folder for users in users_folders for folder in _account_folders(users)]
    lists = [account / LOCAL_FILES for account in accounts if (account / LOCAL_FILES).is_file()]
    if lists:
        return max(lists, key=_modified)
    if accounts:
        return accounts[0]
    log.info("Spotify's folder for local files wasn't found in %d places", len(users_folders))
    return None


def _account_folders(users: Path) -> list[Path]:
    """The account folders in one of Spotify's Users folders."""
    try:
        entries = sorted(users.iterdir())
    except OSError:
        # Most of the places looked in don't exist, as Spotify is installed only one way.
        return []
    return [
        entry for entry in entries if entry.name.endswith(USER_FOLDER_SUFFIX) and entry.is_dir()
    ]


def _modified(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0
