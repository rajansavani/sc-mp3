"""The Python API the web UI calls as `window.pywebview.api`, with one mixin per area."""

from typing import Any

from scmp3.app.bridge.account import AccountApi
from scmp3.app.bridge.components import ComponentsApi
from scmp3.app.bridge.files import FilesApi
from scmp3.app.bridge.jobs import JobsApi
from scmp3.app.bridge.settings import SettingsApi
from scmp3.app.context import AppContext


class Api(JobsApi, FilesApi, SettingsApi, AccountApi, ComponentsApi):
    """Every call the UI can make. A new call goes in its area's mixin, and nowhere else.

    pywebview exposes every public method, and the methods of every public attribute, so the
    context stays private. Mixins read their collaborators from it.
    """

    def __init__(self, context: AppContext) -> None:
        self._context = context

    def snapshot(self) -> dict[str, Any]:
        """Everything the UI shows at startup, with each area adding its own keys."""
        state: dict[str, Any] = {}
        for area in Api.__bases__:
            state.update(area._snapshot_part(self))
        return state
