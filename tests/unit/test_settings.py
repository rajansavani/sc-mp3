"""Tests for the settings defaults and for loading and saving settings.json."""

import json
import logging
import os
import threading
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any, get_args

import pytest

from scmp3.core import paths, settings
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.models import Settings, TitleStyle
from scmp3.core.settings import InvalidSettings, SettingsStore

# Every option in the options menu with its default, in the menu's order. Settings() must match
# this exactly, so a new or changed default has to be made in both places.
MENU_DEFAULTS: dict[str, Any] = {
    # Library: ~/Music/sc-mp3, filled in by the test from paths.default_library_dir().
    "library_dir": None,
    # None means the title style's default file name.
    "filename_template": None,
    "when_file_exists": "ask",
    "artist_folders": True,
    "album_subfolders": False,
    # Title style
    "title_style": "artist_field",
    "custom_title_template": "{title}{version}",
    "custom_artist_template": "{artists}",
    "artist_separator": ", ",
    "feat_wording": "feat.",
    "remove_producer_credits": True,
    "remove_promo_text": True,
    "custom_words": [],
    "singles_album": "title",
    "singles_album_text": "",
    # Audio
    "audio_format": "mp3_320",
    "keep_lossless_originals": False,
    # Cover art
    "cover_max_size": 1000,
    "save_cover_jpg": False,
    # Tags
    "comment_mode": "source_url",
    # Workflow
    "review_before_saving": True,
    "download_while_reviewing": True,
    "offer_clipboard_links": True,
    "retag_playlist_warning": True,
    "concurrent_downloads": 2,
    # Engine and updates
    "check_app_updates": True,
    "check_engine_updates": True,
    "engine_channel": "stable",
    "soundcloud_client_id": None,
}

# Settings kept in settings.json that aren't options in the menu.
HIDDEN_DEFAULTS: dict[str, Any] = {
    "first_run_done": False,
}

FIXED_NOW = datetime(2026, 10, 18, 15, 30, 0)


def test_settings_defaults_match_the_options_menu(scmp3_home: Path) -> None:
    expected = {**MENU_DEFAULTS, "library_dir": paths.default_library_dir(), **HIDDEN_DEFAULTS}
    assert asdict(Settings()) == expected


def test_the_default_library_is_sc_mp3_in_the_music_folder(scmp3_home: Path) -> None:
    assert Settings().library_dir == scmp3_home / "music" / "sc-mp3"


def test_title_style_choices_are_the_preset_ids() -> None:
    assert get_args(TitleStyle) == ("artist_field", "feat_in_title", "all_in_title", "custom")
    assert settings.invalid_keys({"title_style": "fancy"}) == ["title_style"]


def test_the_default_path_is_in_the_config_folder(scmp3_home: Path) -> None:
    assert settings.default_path() == paths.config_dir() / "settings.json"


@pytest.fixture
def path(scmp3_home: Path) -> Path:
    return settings.default_path()


def store_at(path: Path, **kwargs: Any) -> SettingsStore:
    return SettingsStore(path, clock=lambda: FIXED_NOW, **kwargs)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_no_file_gives_the_defaults_and_writes_nothing(path: Path) -> None:
    assert store_at(path).load() == Settings()
    assert not path.exists()


def test_saved_settings_load_back_the_same(path: Path) -> None:
    changed = replace(
        Settings(), title_style="feat_in_title", custom_words=["FREE DL"], cover_max_size=640
    )
    store_at(path).save(changed)
    assert store_at(path).load() == changed


def test_the_file_records_the_schema_version(path: Path) -> None:
    store_at(path).save(Settings())
    assert read_json(path)["version"] == settings.SCHEMA_VERSION


def test_unicode_in_settings_is_written_as_utf8(path: Path, scmp3_home: Path) -> None:
    changed = replace(Settings(), library_dir=scmp3_home / "Musik" / "Sälen", custom_words=["夜"])
    store_at(path).save(changed)
    assert "Sälen" in path.read_text(encoding="utf-8")
    assert store_at(path).load() == changed


def test_save_leaves_no_temp_file_behind(path: Path) -> None:
    store_at(path).save(Settings())
    store_at(path).save(replace(Settings(), concurrent_downloads=3))
    assert [entry.name for entry in path.parent.iterdir()] == ["settings.json"]


def test_save_replaces_the_file_from_a_temp_file_in_the_same_folder(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replaced: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(src: str | Path, dst: str | Path) -> None:
        replaced.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(paths.os, "replace", recording_replace)
    store_at(path).save(Settings())
    [(temp, target)] = replaced
    assert target == path
    assert temp.parent == path.parent
    assert temp != path


def test_a_failed_save_keeps_the_old_file_and_cleans_up(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_at(path).save(Settings())
    before = path.read_bytes()

    def failing_replace(src: str | Path, dst: str | Path) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(paths.os, "replace", failing_replace)
    with pytest.raises(ScmpError) as error_info:
        store_at(path).save(replace(Settings(), concurrent_downloads=5))
    assert error_info.value.kind is ErrorKind.disk
    assert path.read_bytes() == before
    assert [entry.name for entry in path.parent.iterdir()] == ["settings.json"]


def test_unknown_keys_survive_a_save(path: Path) -> None:
    write_json(path, {"version": 1, "cover_max_size": 640, "added_later": {"on": True}})
    store = store_at(path)
    loaded = store.load()
    assert loaded.cover_max_size == 640
    store.save(replace(loaded, cover_max_size=300))
    saved = read_json(path)
    assert saved["added_later"] == {"on": True}
    assert saved["cover_max_size"] == 300


def test_migrations_run_in_order_up_to_the_current_version(path: Path) -> None:
    calls: list[int] = []

    def rename_folder_key(data: dict[str, Any]) -> dict[str, Any]:
        calls.append(1)
        data["library_dir"] = data.pop("folder")
        return data

    def add_words(data: dict[str, Any]) -> dict[str, Any]:
        calls.append(2)
        data["custom_words"] = ["migrated"]
        return data

    folder = str(path.parent / "Old music")
    write_json(path, {"version": 1, "folder": folder})
    store = store_at(path, version=3, migrations={1: rename_folder_key, 2: add_words})
    loaded = store.load()
    assert calls == [1, 2]
    assert loaded.library_dir == Path(folder)
    assert loaded.custom_words == ["migrated"]
    store.save(loaded)
    assert read_json(path)["version"] == 3


def test_a_current_file_runs_no_migration(path: Path) -> None:
    def fail(data: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("a current file was migrated")

    write_json(path, {"version": 2, "cover_max_size": 640})
    assert store_at(path, version=2, migrations={1: fail}).load().cover_max_size == 640


def test_a_file_from_a_newer_version_keeps_its_version(path: Path) -> None:
    write_json(path, {"version": settings.SCHEMA_VERSION + 4, "cover_max_size": 640})
    store = store_at(path)
    store.save(store.load())
    assert read_json(path)["version"] == settings.SCHEMA_VERSION + 4


def test_every_older_version_has_a_migration() -> None:
    assert set(settings.MIGRATIONS) == set(range(1, settings.SCHEMA_VERSION))


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[1, 2, 3]",
        '{"version": "one"}',
        '{"version": 0}',
        b"\xff\xfe\x00 not utf-8",
    ],
    ids=["broken JSON", "not an object", "text version", "version zero", "not UTF-8"],
)
def test_an_unreadable_file_is_moved_aside_and_the_defaults_are_used(
    path: Path, content: str | bytes, caplog: pytest.LogCaptureFixture
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = content.encode() if isinstance(content, str) else content
    path.write_bytes(raw)
    with caplog.at_level(logging.WARNING, logger=settings.__name__):
        loaded = store_at(path).load()
    assert loaded == Settings()
    backup = path.with_name("settings.bad-20261018-153000.json")
    assert backup.read_bytes() == raw
    assert not path.exists()
    assert "settings.bad-20261018-153000.json" in caplog.text


def test_a_second_bad_file_in_the_same_second_gets_its_own_backup(path: Path) -> None:
    write_json(path, [])
    store_at(path).load()
    write_json(path, "again")
    store_at(path).load()
    names = sorted(entry.name for entry in path.parent.iterdir())
    assert names == ["settings.bad-20261018-153000-2.json", "settings.bad-20261018-153000.json"]


def test_a_failed_migration_counts_as_unreadable(path: Path) -> None:
    def broken(data: dict[str, Any]) -> dict[str, Any]:
        raise KeyError("missing")

    write_json(path, {"version": 1})
    assert store_at(path, version=2, migrations={1: broken}).load() == Settings()
    assert path.with_name("settings.bad-20261018-153000.json").exists()


def test_unusable_values_fall_back_one_by_one(path: Path, caplog: pytest.LogCaptureFixture) -> None:
    write_json(
        path,
        {
            "version": 1,
            "title_style": "feat_in_title",
            "cover_max_size": 999,
            "concurrent_downloads": "two",
            "remove_promo_text": 1,
            "custom_words": ["fine", 3],
            "library_dir": 42,
        },
    )
    with caplog.at_level(logging.WARNING, logger=settings.__name__):
        loaded = store_at(path).load()
    assert loaded == replace(Settings(), title_style="feat_in_title")
    for key in (
        "cover_max_size",
        "concurrent_downloads",
        "remove_promo_text",
        "custom_words",
        "library_dir",
    ):
        assert key in caplog.text
    # Only single values were bad, so the file itself stays.
    assert path.exists()


def test_every_default_passes_validation() -> None:
    assert settings.invalid_keys(Settings().to_dict()) == []


def test_validation_tells_true_apart_from_one() -> None:
    assert settings.invalid_keys({"concurrent_downloads": True}) == ["concurrent_downloads"]
    assert settings.invalid_keys({"cover_max_size": True}) == ["cover_max_size"]


def test_validation_ignores_unknown_keys() -> None:
    assert settings.invalid_keys({"added_later": object()}) == []


def test_optional_settings_accept_none_and_text() -> None:
    assert settings.invalid_keys({"soundcloud_client_id": None}) == []
    assert settings.invalid_keys({"soundcloud_client_id": "abc"}) == []
    assert settings.invalid_keys({"soundcloud_client_id": 5}) == ["soundcloud_client_id"]


def test_a_file_with_a_byte_order_mark_loads(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps({"version": 1, "cover_max_size": 640}).encode()
    path.write_bytes(b"\xef\xbb\xbf" + data)
    assert store_at(path).load().cover_max_size == 640
    assert [entry.name for entry in path.parent.iterdir()] == ["settings.json"]


def fail_to_read(monkeypatch: pytest.MonkeyPatch, locked: Path) -> None:
    """Make reading `locked` fail the way a file another program holds open does on Windows."""
    real_read_bytes = Path.read_bytes

    def read_bytes(self: Path) -> bytes:
        if self == locked:
            raise PermissionError(13, "The process cannot access the file")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def test_a_file_that_cant_be_opened_is_left_alone_and_not_overwritten(
    path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    write_json(path, {"version": 1, "cover_max_size": 640})
    # read_text, since read_bytes is the call that fails here.
    before = path.read_text(encoding="utf-8")
    fail_to_read(monkeypatch, path)
    store = store_at(path)
    with caplog.at_level(logging.WARNING, logger=settings.__name__):
        assert store.load() == Settings()
    assert "won't be saved" in caplog.text
    with pytest.raises(ScmpError) as error_info:
        store.save(replace(Settings(), cover_max_size=300))
    assert error_info.value.kind is ErrorKind.disk
    assert path.read_text(encoding="utf-8") == before
    assert [entry.name for entry in path.parent.iterdir()] == ["settings.json"]


def test_saving_works_again_once_the_file_can_be_read(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_json(path, {"version": 1, "cover_max_size": 640})
    store = store_at(path)
    with monkeypatch.context() as patch:
        fail_to_read(patch, path)
        store.load()
    assert store.load().cover_max_size == 640
    store.save(replace(Settings(), cover_max_size=300))
    assert read_json(path)["cover_max_size"] == 300


def test_a_bad_file_that_cant_be_moved_aside_is_not_overwritten(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    def failing_replace(src: str | Path, dst: str | Path) -> None:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(settings.os, "replace", failing_replace)
    store = store_at(path)
    assert store.load() == Settings()
    with pytest.raises(ScmpError):
        store.save(Settings())
    assert path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("count", [1, 2, 3, 4])
def test_downloads_at_once_accepts_one_to_four(count: int) -> None:
    assert settings.invalid_keys({"concurrent_downloads": count}) == []


@pytest.mark.parametrize("count", [0, -1, 5, 2.0])
def test_downloads_at_once_rejects_other_numbers(count: object) -> None:
    assert settings.invalid_keys({"concurrent_downloads": count}) == ["concurrent_downloads"]


@pytest.mark.parametrize("folder", ["", ".", "Music/sc-mp3", "~/Music"])
def test_the_library_folder_has_to_be_a_full_path(folder: str) -> None:
    assert settings.invalid_keys({"library_dir": folder}) == ["library_dir"]


def test_a_full_library_path_is_accepted(scmp3_home: Path) -> None:
    assert settings.invalid_keys({"library_dir": str(scmp3_home / "Songs")}) == []


@pytest.mark.parametrize("mode", ["source_url", "description", "none"])
def test_comment_mode_choices(mode: str) -> None:
    assert settings.invalid_keys({"comment_mode": mode}) == []


def test_filename_template_accepts_none_for_the_styles_default() -> None:
    assert settings.invalid_keys({"filename_template": None}) == []
    assert settings.invalid_keys({"filename_template": "{title}"}) == []


def test_update_applies_a_patch_and_saves_it(path: Path) -> None:
    write_json(path, {"version": 1, "cover_max_size": 640, "added_later": 1})
    store = store_at(path)
    updated = store.update({"title_style": "all_in_title", "custom_words": ["FREE DL"]})
    assert updated == replace(
        Settings(), cover_max_size=640, title_style="all_in_title", custom_words=["FREE DL"]
    )
    saved = read_json(path)
    assert saved["title_style"] == "all_in_title"
    assert saved["cover_max_size"] == 640
    assert saved["added_later"] == 1


def test_update_takes_paths_as_strings(path: Path, scmp3_home: Path) -> None:
    folder = scmp3_home / "Songs"
    assert store_at(path).update({"library_dir": str(folder)}).library_dir == folder


def test_update_rejects_unknown_names_and_unusable_values_and_saves_nothing(path: Path) -> None:
    write_json(path, {"version": 1, "cover_max_size": 640})
    before = path.read_bytes()
    with pytest.raises(InvalidSettings) as error_info:
        store_at(path).update({"made_up": 1, "cover_max_size": 999, "title_style": "custom"})
    assert error_info.value.keys == ["made_up", "cover_max_size"]
    assert path.read_bytes() == before


def test_update_refuses_to_save_over_a_file_it_couldnt_read(
    path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_json(path, {"version": 1, "cover_max_size": 640})
    # read_text, since read_bytes is the call that fails here.
    before = path.read_text(encoding="utf-8")
    fail_to_read(monkeypatch, path)
    with pytest.raises(ScmpError):
        store_at(path).update({"cover_max_size": 300})
    assert path.read_text(encoding="utf-8") == before


def test_updates_from_many_threads_all_land(path: Path) -> None:
    store = store_at(path)
    keys = [
        "artist_folders",
        "album_subfolders",
        "keep_lossless_originals",
        "save_cover_jpg",
        "first_run_done",
        "remove_producer_credits",
        "remove_promo_text",
        "review_before_saving",
        "offer_clipboard_links",
    ]
    defaults = Settings().to_dict()
    threads = [
        threading.Thread(target=store.update, args=({key: not defaults[key]},)) for key in keys
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    saved = read_json(path)
    assert {key: saved[key] for key in keys} == {key: not defaults[key] for key in keys}
