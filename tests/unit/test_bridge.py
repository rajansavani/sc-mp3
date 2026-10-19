"""Tests for the bridge: every call exists, the stubs answer not_implemented, and snapshot()."""

import inspect
import json
import re
import subprocess
import sys
from typing import Any

import pytest

from scmp3 import __version__
from scmp3.app.bridge import Api
from scmp3.app.bridge.account import AccountApi
from scmp3.app.bridge.components import ComponentsApi
from scmp3.app.bridge.files import FilesApi
from scmp3.app.bridge.jobs import JobsApi
from scmp3.app.bridge.settings import SettingsApi
from scmp3.app.context import AppContext
from scmp3.app.events import EventBus
from scmp3.core import paths
from scmp3.core.history import History
from scmp3.core.models import Settings
from scmp3.core.settings import SettingsStore

# Every call the UI can make, by the mixin it belongs in.
CALLS: dict[type, set[str]] = {
    JobsApi: {
        "add_inputs",
        "update_fields",
        "reset_field",
        "toggle_change",
        "set_style",
        "set_artwork",
        "save",
        "skip",
        "retry",
        "cancel",
        "remove",
        "set_album_mode",
        "set_included",
        "history",
        "diagnostics",
    },
    FilesApi: {"pick_folder", "pick_audio_files", "pick_image", "reveal", "open_spotify"},
    SettingsApi: {"update_settings", "preview_render"},
    AccountApi: {"sign_in_window", "sign_in_firefox", "sign_in_token", "sign_out"},
    ComponentsApi: {"check_updates", "update_engine", "revert_engine", "install_youtube_support"},
}

STUBS = sorted(name for names in CALLS.values() for name in names)

SNAKE_CASE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*")


def public_names(obj: Any) -> set[str]:
    return {name for name in dir(obj) if not name.startswith("_")}


@pytest.fixture
def store() -> SettingsStore:
    return SettingsStore(paths.config_dir() / "settings.json")


@pytest.fixture
def api(store: SettingsStore) -> Api:
    context = AppContext(
        settings=store,
        history=History(paths.data_dir() / "history.db"),
        events=EventBus(),
        engine_version=lambda: "2026.08.19",
    )
    return Api(context)


@pytest.mark.parametrize("area", list(CALLS))
def test_each_mixin_has_exactly_its_areas_calls(area: type) -> None:
    assert public_names(area) == CALLS[area]


def test_the_api_offers_every_call_plus_snapshot(api: Api) -> None:
    assert public_names(api) == {"snapshot", *STUBS}


def test_the_api_has_no_public_attributes_pywebview_would_expose(api: Api) -> None:
    # pywebview exposes the methods of public attributes too, such as a settings store's save().
    for name in public_names(api):
        assert inspect.ismethod(getattr(api, name)), name


@pytest.mark.parametrize("name", STUBS)
def test_every_call_not_built_yet_says_so(api: Api, name: str) -> None:
    method = getattr(api, name)
    required = [
        parameter
        for parameter in inspect.signature(method).parameters.values()
        if parameter.default is inspect.Parameter.empty
    ]
    assert method(*[None] * len(required)) == {"error": "not_implemented"}


def test_a_method_added_to_one_mixin_reaches_the_ui(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FilesApi, "pick_cover", lambda self: {"path": "cover.png"}, raising=False)
    assert "pick_cover" in public_names(api)
    assert api.pick_cover() == {"path": "cover.png"}  # type: ignore[attr-defined]


def test_a_snapshot_part_added_to_one_mixin_reaches_the_snapshot(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(FilesApi, "_snapshot_part", lambda self: {"recent_folders": []})
    assert api.snapshot()["recent_folders"] == []


def test_the_snapshot_has_every_part(api: Api) -> None:
    snapshot = api.snapshot()
    assert set(snapshot) == {"jobs", "settings", "account", "components", "first_run"}
    assert snapshot["jobs"] == []
    assert snapshot["account"] is None
    assert snapshot["components"] == {"app_version": __version__, "engine_version": "2026.08.19"}


def test_the_snapshot_carries_the_saved_settings(api: Api, store: SettingsStore) -> None:
    store.update({"concurrent_downloads": 3})
    assert api.snapshot()["settings"] == {**Settings().to_dict(), "concurrent_downloads": 3}


def test_first_run_is_true_until_setup_is_done(api: Api, store: SettingsStore) -> None:
    assert api.snapshot()["first_run"] is True
    store.update({"first_run_done": True})
    assert api.snapshot()["first_run"] is False


def test_the_snapshot_is_json_with_snake_case_keys(api: Api) -> None:
    snapshot = json.loads(json.dumps(api.snapshot()))
    keys = [*snapshot, *snapshot["settings"], *snapshot["components"]]
    assert all(SNAKE_CASE.fullmatch(key) for key in keys), keys


def test_the_snapshot_parts_dont_overlap(api: Api) -> None:
    # A key two areas both set would silently lose one of them.
    keys = [key for area in Api.__bases__ for key in area._snapshot_part(api)]
    assert len(keys) == len(set(keys))


def test_the_snapshot_doesnt_import_the_engine() -> None:
    # A fresh interpreter, since this test session has imported yt-dlp already.
    script = "\n".join(
        [
            "import sys",
            "from scmp3.app.bridge import Api",
            "from scmp3.app.context import AppContext",
            "snapshot = Api(AppContext.from_app_folders()).snapshot()",
            "print(snapshot['components']['engine_version'] is not None, 'yt_dlp' in sys.modules)",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=True
    )
    assert result.stdout.split() == ["True", "False"]
