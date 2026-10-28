"""Every option in the options menu with its default, and the choices each one offers."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from scmp3.core.models.base import Model
from scmp3.core.paths import default_library_dir

# The choices offered in the options menu.
TitleStyle = Literal["artist_field", "feat_in_title", "all_in_title", "custom"]
CollisionPolicy = Literal["ask", "keep_both", "replace", "skip"]
ArtistSeparator = Literal[", ", " & ", " x "]
FeatWording = Literal["feat.", "ft.", "featuring", "with"]
SinglesAlbum = Literal["title", "fixed", "blank"]
AudioFormat = Literal["mp3_320", "mp3_v0", "m4a"]
CoverSize = Literal[300, 640, 1000, 1400]
CommentMode = Literal["source_url", "description", "none"]
DownloadsAtOnce = Literal[1, 2, 3, 4]
EngineChannel = Literal["stable", "nightly"]


@dataclass
class Settings(Model):
    """Every option in the options menu with its default, plus first-run progress."""

    # Library
    library_dir: Path = field(default_factory=default_library_dir)
    # None means the title style's own default: {title} for all_in_title, else {artist} - {title}.
    filename_template: str | None = None
    when_file_exists: CollisionPolicy = "ask"
    artist_folders: bool = True
    album_subfolders: bool = False
    # Title style. First-run setup asks for the style, so its default only applies until then.
    title_style: TitleStyle = "artist_field"
    custom_title_template: str = "{title}{version}"
    custom_artist_template: str = "{artists}"
    artist_separator: ArtistSeparator = ", "
    feat_wording: FeatWording = "feat."
    remove_producer_credits: bool = True
    remove_promo_text: bool = True
    custom_words: list[str] = field(default_factory=list)
    singles_album: SinglesAlbum = "title"
    singles_album_text: str = ""  # used when singles_album is "fixed"
    # Audio
    audio_format: AudioFormat = "mp3_320"
    keep_lossless_originals: bool = False
    # Cover art
    cover_max_size: CoverSize = 1000
    save_cover_jpg: bool = False
    # Tags
    comment_mode: CommentMode = "source_url"
    # Workflow
    review_before_saving: bool = True
    download_while_reviewing: bool = True
    offer_clipboard_links: bool = True
    # Before a re-tag changes a song's title, artist or album, warn that it drops out of playlists.
    retag_playlist_warning: bool = True
    concurrent_downloads: DownloadsAtOnce = 2
    # Engine and updates
    check_app_updates: bool = True
    check_engine_updates: bool = True
    engine_channel: EngineChannel = "stable"
    soundcloud_client_id: str | None = None  # overrides the one yt-dlp finds
    # Not in the menu: set when first-run setup ends, so it isn't shown again.
    first_run_done: bool = False
