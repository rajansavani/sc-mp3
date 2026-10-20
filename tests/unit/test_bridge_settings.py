"""Tests for the settings calls: saving with per-field problems, first-run, and live previews."""

import getpass
import json
import logging
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from scmp3.app.bridge import Api
from scmp3.app.bridge import settings as bridge
from scmp3.app.context import AppContext
from scmp3.app.events import EventBus
from scmp3.app.main import WEB_DIR
from scmp3.core import library, paths
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.history import History
from scmp3.core.models import Settings
from scmp3.core.settings import SettingsStore

# A value other than the default for every option in PLAN §4.9 that the options menu shows.
MENU_CHANGES: dict[str, Any] = {
    "filename_template": "{track:02} {artist} - {title} ({year})",
    "when_file_exists": "keep_both",
    "artist_folders": False,
    "album_subfolders": True,
    "title_style": "custom",
    "custom_title_template": "{title} (ft. {featured}){version}",
    "custom_artist_template": "{main}",
    "artist_separator": " x ",
    "feat_wording": "featuring",
    "remove_producer_credits": False,
    "remove_promo_text": False,
    "custom_words": ["[Bass Boosted]", "Sped Up Edit"],
    "singles_album": "fixed",
    "singles_album_text": "Loose Tracks",
    "audio_format": "m4a",
    "keep_lossless_originals": True,
    "cover_max_size": 1400,
    "save_cover_jpg": True,
    "comment_mode": "description",
    "review_before_saving": False,
    "download_while_reviewing": False,
    "offer_clipboard_links": False,
    "concurrent_downloads": 4,
}


@pytest.fixture
def store() -> SettingsStore:
    return SettingsStore(paths.config_dir() / "settings.json")


def make_api(store: SettingsStore) -> Api:
    context = AppContext(
        settings=store,
        history=History(paths.data_dir() / "history.db"),
        events=EventBus(),
        engine_version=lambda: "2026.08.19",
    )
    return Api(context)


@pytest.fixture
def api(store: SettingsStore) -> Api:
    return make_api(store)


def restarted(store: SettingsStore) -> Api:
    """A new Api on a new store for the same file, as after closing and opening the app."""
    return make_api(SettingsStore(store.path))


def problem(api: Api, patch: dict[str, Any], key: str) -> dict[str, str]:
    result = api.update_settings(patch)
    assert result["error"] == "invalid_settings", result
    return result["fields"][key]


class DiskFullStore(SettingsStore):
    """A store whose disk is full."""

    def save(self, settings: Settings) -> None:
        raise ScmpError(ErrorKind.disk, "can't write settings.json: No space left on device")


# Saving


def test_a_patch_is_saved_and_the_saved_settings_come_back(api: Api, store: SettingsStore) -> None:
    result = api.update_settings({"concurrent_downloads": 3})
    assert result == {"settings": {**Settings().to_dict(), "concurrent_downloads": 3}}
    assert store.load().concurrent_downloads == 3


@pytest.mark.parametrize(("key", "value"), list(MENU_CHANGES.items()))
def test_every_menu_option_can_be_changed_and_survives_a_restart(
    api: Api, store: SettingsStore, key: str, value: Any
) -> None:
    assert Settings().to_dict()[key] != value
    assert api.update_settings({key: value})["settings"][key] == value
    assert restarted(store).snapshot()["settings"][key] == value


def test_all_the_changes_at_once_survive_a_restart(api: Api, store: SettingsStore) -> None:
    assert "error" not in api.update_settings(MENU_CHANGES)
    saved = restarted(store).snapshot()["settings"]
    assert {key: saved[key] for key in MENU_CHANGES} == MENU_CHANGES


def test_a_new_library_folder_survives_a_restart(
    api: Api, store: SettingsStore, tmp_path: Path
) -> None:
    folder = tmp_path / "Songs"
    assert "error" not in api.update_settings({"library_dir": str(folder)})
    assert restarted(store).snapshot()["settings"]["library_dir"] == str(folder)


def test_an_unknown_setting_is_refused_and_nothing_is_saved(api: Api, store: SettingsStore) -> None:
    result = api.update_settings({"concurrent_downloads": 3, "colour_theme": "light"})
    assert result == {
        "error": "invalid_settings",
        "fields": {"colour_theme": {"reason": "UNKNOWN_SETTING"}},
    }
    assert not store.path.exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("when_file_exists", "sometimes"),
        ("title_style", "upside_down"),
        ("artist_separator", " / "),
        ("feat_wording", "feat"),
        ("singles_album", "none"),
        ("audio_format", "flac"),
        ("cover_max_size", 500),
        ("cover_max_size", "1000"),
        ("comment_mode", "both"),
        ("concurrent_downloads", 5),
        ("concurrent_downloads", True),
        ("album_subfolders", 1),
        ("custom_words", "one word"),
        ("custom_words", [1, 2]),
        ("filename_template", 7),
        ("library_dir", None),
        ("first_run_done", "yes"),
    ],
)
def test_a_value_that_isnt_one_of_the_choices_is_refused(
    api: Api, store: SettingsStore, key: str, value: Any
) -> None:
    assert problem(api, {key: value}, key) == {"reason": "INVALID_VALUE"}
    assert not store.path.exists()


def test_one_bad_value_keeps_the_whole_patch_from_saving(api: Api, store: SettingsStore) -> None:
    result = api.update_settings({"concurrent_downloads": 3, "cover_max_size": 999})
    assert result["fields"] == {"cover_max_size": {"reason": "INVALID_VALUE"}}
    assert store.load().concurrent_downloads == 2


@pytest.mark.parametrize("patch", [None, ["concurrent_downloads"], {3: "x"}, "first_run_done"])
def test_a_patch_that_isnt_a_dict_of_names_is_refused(api: Api, patch: Any) -> None:
    assert api.update_settings(patch) == {"error": "invalid_settings", "fields": {}}


def test_a_disk_error_comes_back_as_its_kind(tmp_path: Path) -> None:
    api = make_api(DiskFullStore(tmp_path / "settings.json"))
    assert api.update_settings({"concurrent_downloads": 3}) == {"error": "disk"}


def test_settings_that_couldnt_be_read_are_not_saved_over(store: SettingsStore) -> None:
    store.path.parent.mkdir(parents=True)
    # A folder where the file should be can't be opened as one, so the store refuses to save.
    store.path.mkdir()
    api = make_api(store)
    assert api.update_settings({"concurrent_downloads": 3}) == {"error": "disk"}


# File name and title templates


@pytest.mark.parametrize(
    "template",
    [
        "{title}",
        "{artist} - {title} ({year})",
        "{track:02} {title}",
        "{album_artist} - {album} - {track} - {title} ({year})",
        "{artist}{title}",
    ],
)
def test_a_filename_template_with_library_tokens_is_saved(api: Api, template: str) -> None:
    saved = api.update_settings({"filename_template": template})["settings"]
    assert saved["filename_template"] == template


@pytest.mark.parametrize("template", ["", "   "])
def test_an_empty_filename_template_means_the_styles_default(api: Api, template: str) -> None:
    api.update_settings({"filename_template": "{title}"})
    saved = api.update_settings({"filename_template": template})["settings"]
    assert saved["filename_template"] is None


def test_typing_the_styles_own_template_saves_none_so_the_name_follows_the_style(
    api: Api,
) -> None:
    api.update_settings({"filename_template": "{title}"})
    saved = api.update_settings({"filename_template": "{artist} - {title}"})["settings"]
    assert saved["filename_template"] is None
    api.update_settings({"title_style": "all_in_title"})
    preview = api.preview_render()
    assert (preview["filename"], preview["filename_template"]) == (
        "Kaia/Midnight Drive (Kaia, Lou V).mp3",
        "{title}",
    )


def test_the_style_in_the_same_patch_decides_which_template_is_its_own(api: Api) -> None:
    patch = {"title_style": "all_in_title", "filename_template": "{title}"}
    assert api.update_settings(patch)["settings"]["filename_template"] is None
    # Under all_in_title, the other styles' default is a template of its own.
    saved = api.update_settings({"filename_template": "{artist} - {title}"})["settings"]
    assert saved["filename_template"] == "{artist} - {title}"


@pytest.mark.parametrize(
    ("template", "token"),
    [
        ("{artist} - {feat}", "{feat}"),
        ("{Artist} - {title}", "{Artist}"),
        ("{artists} - {title}", "{artists}"),
        ("{title} {}", "{}"),
        ("{title:2}", "{title:2}"),
    ],
)
def test_a_filename_template_with_an_unknown_token_names_it(
    api: Api, store: SettingsStore, template: str, token: str
) -> None:
    assert problem(api, {"filename_template": template}, "filename_template") == {
        "reason": "UNKNOWN_TOKEN",
        "token": token,
    }
    assert not store.path.exists()


@pytest.mark.parametrize("template", ["{artist} - {title", "{artist} - title}", "{{title}}"])
def test_a_brace_without_its_partner_is_refused(api: Api, template: str) -> None:
    key = "filename_template"
    assert problem(api, {key: template}, key) == {"reason": "OPEN_BRACE"}


@pytest.mark.parametrize("key", ["filename_template", "custom_title_template"])
def test_a_template_without_any_token_is_refused(api: Api, key: str) -> None:
    assert problem(api, {key: "My song"}, key) == {"reason": "NO_TOKENS"}


@pytest.mark.parametrize("key", ["custom_title_template", "custom_artist_template"])
@pytest.mark.parametrize(
    "template",
    ["{title}{version}", "{title} (ft. {featured}){version}", "{main}", "{artists}", "{featured}"],
)
def test_custom_templates_take_the_five_suggestion_tokens(
    api: Api, key: str, template: str
) -> None:
    assert api.update_settings({key: template})["settings"][key] == template


def test_the_template_tokens_are_the_ones_the_engine_fills_in() -> None:
    assert {"title", "version", "main", "featured", "artists"} == bridge.TITLE_TOKENS


@pytest.mark.parametrize("key", ["custom_title_template", "custom_artist_template"])
def test_feat_in_a_custom_template_says_to_write_the_wording_around_featured(
    api: Api, key: str
) -> None:
    assert problem(api, {key: "{title} ({feat} {featured})"}, key) == {"reason": "FEAT_TOKEN"}


@pytest.mark.parametrize(
    ("template", "token"), [("{artist}", "{artist}"), ("{title:02}", "{title:02}")]
)
def test_custom_templates_refuse_the_file_name_tokens(api: Api, template: str, token: str) -> None:
    key = "custom_artist_template"
    assert problem(api, {key: template}, key) == {"reason": "UNKNOWN_TOKEN", "token": token}


# The library folder


def test_a_missing_library_folder_is_created(api: Api, tmp_path: Path) -> None:
    folder = tmp_path / "Music" / "sc-mp3"
    assert "error" not in api.update_settings({"library_dir": str(folder)})
    assert folder.is_dir()


def test_an_existing_library_folder_is_kept_as_it_is(api: Api, tmp_path: Path) -> None:
    (tmp_path / "song.mp3").write_bytes(b"ID3")
    assert "error" not in api.update_settings({"library_dir": str(tmp_path)})
    assert [path.name for path in tmp_path.iterdir()] == ["song.mp3"]


def test_a_relative_library_folder_is_refused(api: Api, store: SettingsStore) -> None:
    assert problem(api, {"library_dir": "Music/sc-mp3"}, "library_dir") == {
        "reason": "NOT_ABSOLUTE"
    }
    assert not store.path.exists()


def test_a_file_is_refused_as_the_library_folder(api: Api, tmp_path: Path) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"ID3")
    assert problem(api, {"library_dir": str(song)}, "library_dir") == {
        "reason": "NOT_A_FOLDER",
        "folder": str(song),
    }


def test_a_folder_inside_a_file_cant_be_created(api: Api, tmp_path: Path) -> None:
    song = tmp_path / "song.mp3"
    song.write_bytes(b"ID3")
    folder = song / "sc-mp3"
    assert problem(api, {"library_dir": str(folder)}, "library_dir") == {
        "reason": "CANT_CREATE",
        "folder": str(folder),
    }


def test_a_null_character_in_the_folder_is_refused(api: Api, tmp_path: Path) -> None:
    folder = f"{tmp_path}{bridge.NULL}x"
    assert problem(api, {"library_dir": folder}, "library_dir") == {"reason": "INVALID_VALUE"}


def test_a_folder_that_cant_be_written_to_is_refused(tmp_path: Path) -> None:
    def refuse(folder: Path) -> None:
        raise PermissionError(13, "Access is denied")

    assert bridge.library_dir_problem(str(tmp_path), probe=refuse) == {
        "reason": "NOT_WRITABLE",
        "folder": str(tmp_path),
    }


def test_the_bridge_refuses_a_folder_that_cant_be_written_to(
    api: Api, store: SettingsStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(folder: Path) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(bridge, "probe_writing", refuse)
    assert problem(api, {"library_dir": str(tmp_path)}, "library_dir")["reason"] == "NOT_WRITABLE"
    assert not store.path.exists()


@contextmanager
def refusing_new_files(folder: Path) -> Iterator[None]:
    """Make `folder` refuse new files: a deny entry for this user on Windows, mode 555 elsewhere."""
    if sys.platform == "win32":
        user = getpass.getuser()
        icacls = ["icacls", str(folder)]
        subprocess.run([*icacls, "/deny", f"{user}:(W,AD)"], check=True, capture_output=True)
        try:
            yield
        finally:
            subprocess.run([*icacls, "/remove:d", user], check=True, capture_output=True)
        return
    if os.geteuid() == 0:
        pytest.skip("root can write to any folder")
    folder.chmod(0o555)
    try:
        yield
    finally:
        folder.chmod(0o755)


def test_a_folder_that_refuses_new_files_is_refused_at_once(api: Api, tmp_path: Path) -> None:
    folder = tmp_path / "locked"
    folder.mkdir()
    started = time.monotonic()
    with refusing_new_files(folder):
        result = api.update_settings({"library_dir": str(folder)})
    # tempfile.mkstemp never returned here on Windows, retrying a name it thought was taken.
    assert time.monotonic() - started < 5
    assert result["fields"]["library_dir"]["reason"] == "NOT_WRITABLE"
    assert list(folder.iterdir()) == []


def test_settings_in_a_folder_that_refuses_new_files_fail_at_once_as_a_disk_error(
    api: Api, store: SettingsStore
) -> None:
    api.update_settings({"concurrent_downloads": 3})
    before = store.path.read_bytes()
    started = time.monotonic()
    with refusing_new_files(store.path.parent):
        result = api.update_settings({"concurrent_downloads": 4})
    assert time.monotonic() - started < 5
    assert result == {"error": "disk"}
    assert store.path.read_bytes() == before
    assert [path.name for path in store.path.parent.iterdir()] == ["settings.json"]


def test_a_folder_that_keeps_the_check_file_is_refused_with_a_warning_and_no_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    attempts: list[Path] = []

    def refuse_to_delete(path: Path, missing_ok: bool = False) -> None:
        attempts.append(path)
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(Path, "unlink", refuse_to_delete)
    with caplog.at_level(logging.WARNING, logger=bridge.__name__):
        problem = bridge.library_dir_problem(str(tmp_path))
    assert problem == {"reason": "NOT_WRITABLE", "folder": str(tmp_path)}
    assert len(attempts) == 1
    assert caplog.messages == [
        "The library folder check couldn't remove its own file: Access is denied"
    ]
    assert str(tmp_path) not in caplog.text


def test_the_write_check_leaves_nothing_behind(tmp_path: Path) -> None:
    bridge.probe_writing(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_the_folder_isnt_created_when_another_value_is_bad(api: Api, tmp_path: Path) -> None:
    folder = tmp_path / "sc-mp3"
    result = api.update_settings({"library_dir": str(folder), "cover_max_size": 2})
    assert set(result["fields"]) == {"cover_max_size"}
    assert not folder.exists()


# First-run setup


def test_first_run_ends_when_setup_saves_that_its_done(api: Api, store: SettingsStore) -> None:
    assert api.snapshot()["first_run"] is True
    result = api.update_settings({"first_run_done": True})
    assert result["settings"]["first_run_done"] is True
    assert api.snapshot()["first_run"] is False
    assert restarted(store).snapshot()["first_run"] is False


def test_a_clean_profile_starts_with_first_run(scmp3_home: Path) -> None:
    assert list(scmp3_home.iterdir()) == []
    assert Api(AppContext.from_app_folders()).snapshot()["first_run"] is True


# Previews


@pytest.mark.parametrize(
    ("style", "title", "artist", "filename"),
    [
        (None, "Midnight Drive", "Kaia, Lou V", "Kaia/Kaia, Lou V - Midnight Drive.mp3"),
        ("artist_field", "Midnight Drive", "Kaia, Lou V", "Kaia/Kaia, Lou V - Midnight Drive.mp3"),
        (
            "feat_in_title",
            "Midnight Drive (feat. Lou V)",
            "Kaia",
            "Kaia/Kaia - Midnight Drive (feat. Lou V).mp3",
        ),
        (
            "all_in_title",
            "Midnight Drive (Kaia, Lou V)",
            "Kaia",
            "Kaia/Midnight Drive (Kaia, Lou V).mp3",
        ),
    ],
)
def test_the_preview_shows_the_design_sample_in_each_style(
    api: Api, style: str | None, title: str, artist: str, filename: str
) -> None:
    preview = api.preview_render(None, style)
    assert (preview["title"], preview["artist"], preview["filename"]) == (title, artist, filename)


def test_the_preview_follows_the_saved_title_style(api: Api) -> None:
    api.update_settings({"title_style": "all_in_title"})
    preview = api.preview_render()
    assert preview["title"] == "Midnight Drive (Kaia, Lou V)"
    assert (preview["filename"], preview["filename_template"]) == (
        "Kaia/Midnight Drive (Kaia, Lou V).mp3",
        "{title}",
    )


def test_the_preview_uses_the_separator_and_the_feat_wording(api: Api) -> None:
    api.update_settings({"artist_separator": " & ", "feat_wording": "ft."})
    assert api.preview_render(None, "artist_field")["artist"] == "Kaia & Lou V"
    assert api.preview_render(None, "feat_in_title")["title"] == "Midnight Drive (ft. Lou V)"


def test_the_preview_uses_the_custom_templates(api: Api) -> None:
    api.update_settings(
        {
            "title_style": "custom",
            "custom_title_template": "{title} (ft. {featured}){version}",
            "custom_artist_template": "{main}",
        }
    )
    preview = api.preview_render()
    assert (preview["title"], preview["artist"]) == ("Midnight Drive (ft. Lou V)", "Kaia")


def test_the_preview_keeps_credits_and_promo_when_the_settings_say_so(api: Api) -> None:
    api.update_settings({"remove_producer_credits": False, "remove_promo_text": False})
    assert api.preview_render()["title"] == "Midnight Drive [prod. Hazy] FREE DL"


def test_the_preview_fills_in_the_filename_template(api: Api) -> None:
    api.update_settings({"filename_template": "{year} {album_artist} - {title} [{track}]"})
    preview = api.preview_render()
    assert preview["filename"] == "Kaia/2026 Kaia - Midnight Drive.mp3"
    assert preview["filename_template"] == "{year} {album_artist} - {title} [{track}]"


@pytest.mark.parametrize("style", ["artist_field", "feat_in_title", "all_in_title"])
@pytest.mark.parametrize("singles_album", ["title", "fixed"])
@pytest.mark.parametrize(("artist_folders", "folder"), [(True, "Kaia"), (False, "")])
def test_the_sample_single_stays_out_of_album_folders(
    api: Api, style: str, singles_album: str, artist_folders: bool, folder: str
) -> None:
    api.update_settings(
        {
            "artist_folders": artist_folders,
            "album_subfolders": True,
            "singles_album": singles_album,
            "singles_album_text": "Loose Tracks",
        }
    )
    filename = api.preview_render(None, style)["filename"]
    assert filename.rpartition("/")[0] == folder


def test_the_preview_has_no_folder_with_artist_folders_off(api: Api) -> None:
    api.update_settings({"artist_folders": False})
    assert api.preview_render()["filename"] == "Kaia, Lou V - Midnight Drive.mp3"


def test_the_preview_shows_the_planned_folder_without_listing_the_library(
    api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "KAIA").mkdir()
    api.update_settings({"library_dir": str(tmp_path)})

    def refuse(path: object) -> None:
        pytest.fail("the preview listed the library")

    monkeypatch.setattr(library.os, "scandir", refuse)
    assert api.preview_render()["filename"] == "Kaia/Kaia, Lou V - Midnight Drive.mp3"


def test_the_preview_ends_in_m4a_when_aac_is_kept(api: Api) -> None:
    api.update_settings({"audio_format": "m4a"})
    assert api.preview_render()["filename"] == "Kaia/Kaia, Lou V - Midnight Drive.m4a"


def test_the_preview_takes_a_sample_of_the_callers(api: Api) -> None:
    sample = {"title": "Juice WRLD - Lucid Dreams", "uploader": "Lyrical Lemonade"}
    preview = api.preview_render(sample)
    assert (preview["title"], preview["artist"]) == ("Lucid Dreams", "Juice WRLD")


@pytest.mark.parametrize(
    "sample",
    ["Midnight Drive", {"base_title": "x"}, {"title": 3}, {"artists": "Kaia"}, {"artists": [1]}],
)
def test_the_preview_refuses_an_unusable_sample(api: Api, sample: Any) -> None:
    assert api.preview_render(sample) == {"error": "invalid_sample"}


@pytest.mark.parametrize("style", ["Custom", "feat", ""])
def test_the_preview_refuses_an_unknown_style(api: Api, style: str) -> None:
    assert api.preview_render(None, style) == {"error": "invalid_style"}


def test_the_preview_reports_a_folder_too_deep_for_any_name(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(library, "MAX_PATH_UNITS", 10)
    assert api.preview_render() == {"error": "disk"}


def test_every_problem_the_bridge_names_has_text_in_the_ui() -> None:
    codes = {
        value
        for name, value in vars(bridge).items()
        if name.isupper() and isinstance(value, str) and re.fullmatch("[A-Z_]+", value)
    }
    assert {"UNKNOWN_TOKEN", "NOT_WRITABLE", "FEAT_TOKEN"} <= codes
    strings = (WEB_DIR / "strings" / "settings.js").read_text(encoding="utf-8")
    block = strings[strings.index("fieldErrors: {") :]
    block = block[: block.index("\n  },")]
    texts = set(re.findall(r"^\s+([A-Z_]+):", block, re.MULTILINE))
    assert codes == texts


def test_the_mock_starts_from_every_setting_and_its_default() -> None:
    mock = (WEB_DIR / "mock" / "settings.js").read_text(encoding="utf-8")
    block = mock[mock.index("const DEFAULTS = {") : mock.index("\n};")]
    values = dict(re.findall(r"^  (\w+): (.+),$", block, re.MULTILINE))
    defaults = Settings().to_dict()
    assert values.keys() == defaults.keys()
    # The mock's library folder is a made-up home path, not this machine's.
    for key in defaults.keys() - {"library_dir"}:
        assert json.loads(values[key]) == defaults[key], key


def test_the_answers_are_json(api: Api, tmp_path: Path) -> None:
    answers = [
        api.update_settings({"library_dir": str(tmp_path)}),
        api.update_settings({"filename_template": "{nope}"}),
        api.preview_render(),
    ]
    assert json.loads(json.dumps(answers)) == answers
