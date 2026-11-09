"""Why the latest sign-in or sign-out failed, as the account's view carries it."""

import logging
from dataclasses import dataclass
from typing import Any, Literal

from scmp3.app.signin import WindowSignInFailed
from scmp3.core.errors import ErrorKind, ScmpError
from scmp3.core.tokens import TokenProblem, TokenRefused

Task = Literal["window", "browser", "sign_out"]
Field = Literal["access", "refresh"]
# The kind of a problem with a pasted value's shape. Every other problem is an ErrorKind, or this.
REFUSED = "refused"
# Signing out deleted the tokens, but a kept browser profile stayed under its kept name.
PROFILE_LEFT = "profile_left"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Problem:
    """Why the latest try failed. `id` grows with each one, so the UI shows each one once."""

    id: int
    task: Task
    kind: str  # an ErrorKind, a WindowProblem, REFUSED or PROFILE_LEFT
    field: Field | None = None  # the field a REFUSED value was in
    reason: TokenProblem | None = None  # what's wrong with it

    @classmethod
    def of(cls, id: int, task: Task, kind: str, reason: TokenProblem | None = None) -> "Problem":
        """A problem, with the field a refused value was in taken from its reason."""
        return cls(id, task, kind, _field_of(reason) if reason else None, reason)

    def to_dict(self) -> dict[str, Any]:
        reason = self.reason.value if self.reason else None
        return {
            "id": self.id,
            "task": self.task,
            "kind": self.kind,
            "field": self.field,
            "reason": reason,
        }


def _field_of(problem: TokenProblem) -> Field:
    """The field a refused value was pasted in, from the start of the problem's name."""
    return "access" if problem.value.startswith("access_") else "refresh"


def problem_of(error: Exception, doing: str) -> tuple[str, TokenProblem | None]:
    """The kind and reason of the problem an error made, logged as `doing` SoundCloud failing.

    `doing` is "Signing in to" or "Signing out of".
    """
    if isinstance(error, WindowSignInFailed):
        log.info("The sign-in window gave no sign-in: %s", error.problem)
        return error.problem.value, None
    if isinstance(error, TokenRefused):
        log.info("A pasted value was refused: %s", error.problem)
        return REFUSED, error.problem
    if isinstance(error, ScmpError):
        log.warning("%s SoundCloud failed: %s", doing, error)
        return error.kind, None
    log.error("%s SoundCloud failed unexpectedly", doing, exc_info=error)
    return ErrorKind.internal, None
