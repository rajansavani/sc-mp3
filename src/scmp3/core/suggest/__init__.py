"""The suggestion engine, which proposes cleaned-up tags for a person to confirm.

`suggest` reads a source, `apply_changes` re-applies the change toggles, and `render` turns the
result into the tags to write, in the chosen title style.
"""

from scmp3.core.suggest.render import InvalidOverride, render
from scmp3.core.suggest.suggestion import apply_changes, suggest

__all__ = ["InvalidOverride", "apply_changes", "render", "suggest"]
