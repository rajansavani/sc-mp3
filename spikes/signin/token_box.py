"""A small local window where a person pastes a SoundCloud token. The value stays in memory."""

import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass, field

from token_info import is_access_token

TIMEOUT_SECONDS = 600.0
TITLE = "sc-mp3 token test"
INSTRUCTIONS = (
    "Paste the value of the oauth_token cookie from soundcloud.com, then select OK.\n"
    "It stays in memory while the checks run. Nothing is saved."
)
# Things people paste along with the token: the cookie name, or the header scheme.
PREFIXES = ("oauth_refresh_token=", "oauth_token=", "oauth ")
QUOTES = "\"'"
MAX_ATTEMPTS = 2

# Returns a message to show in the box when a paste is the wrong kind of token, or None.
Rejector = Callable[[str], str | None]


@dataclass
class Submission:
    """What to do with one paste. The token is excluded from repr so it never gets printed."""

    token: str | None = field(default=None, repr=False)
    message: str = ""
    done: bool = True


def clean_token(raw: str) -> str:
    """Strip spaces, quotes and a cookie-name or "OAuth " prefix from a pasted token."""
    text = raw.strip().strip(QUOTES).strip()
    for prefix in PREFIXES:
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip().strip(QUOTES).strip()
    return text


def judge_submission(
    raw: str, reject: Rejector | None, rejections: int, max_attempts: int = MAX_ATTEMPTS
) -> Submission:
    """Accept a paste, or reject it with a message and say whether the box stays open."""
    token = clean_token(raw)
    if not token:
        return Submission()
    problem = reject(token) if reject else None
    if problem is None:
        return Submission(token=token)
    # The last allowed attempt closes the box with no token, so no request is made.
    return Submission(message=problem, done=rejections + 1 >= max_attempts)


def reject_access_token(token: str) -> str | None:
    """Refuse the oauth_token cookie, an at+JWT access token, where a refresh token is wanted."""
    if is_access_token(token):
        return "That's the access token, the oauth_token cookie. Copy the oauth_refresh_token row."
    return None


def reject_non_access_token(token: str) -> str | None:
    """Refuse anything that isn't the oauth_token cookie, where the access token is wanted."""
    if is_access_token(token):
        return None
    return "That's not the access token. Copy the oauth_token row, a long value starting eyJ."


def ask_for_token(
    timeout: float = TIMEOUT_SECONDS,
    title: str = TITLE,
    instructions: str = INSTRUCTIONS,
    reject: Rejector | None = None,
    rejected: list[str] | None = None,
) -> str | None:
    """Show the box until a paste is accepted, it's closed, or `timeout` seconds pass.

    A paste that `reject` refuses shows its message in the box, and the person can paste once
    more. Each refusal message is appended to `rejected`. Returns the token or None.
    """
    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    tk.Label(root, text=instructions, justify="left").pack(padx=16, pady=(16, 8), anchor="w")
    # A password-style field, so the token isn't shown on screen.
    entry = tk.Entry(root, show="*", width=60)
    entry.pack(padx=16, fill="x")
    status = tk.Label(root, text="", fg="#b00020", justify="left", wraplength=480)
    status.pack(padx=16, pady=(8, 0), anchor="w")
    accepted: list[str] = []
    refusals: list[str] = rejected if rejected is not None else []

    def submit(_event: object = None) -> None:
        outcome = judge_submission(entry.get(), reject, len(refusals))
        if outcome.message:
            refusals.append(outcome.message)
        if outcome.token:
            accepted.append(outcome.token)
        if outcome.done:
            root.destroy()
            return
        status.config(text=f"{outcome.message} You can paste once more.")
        entry.delete(0, "end")

    tk.Button(root, text="OK", width=10, command=submit).pack(padx=16, pady=16, anchor="e")
    entry.bind("<Return>", submit)
    root.after(int(timeout * 1000), root.destroy)
    root.lift()
    entry.focus_force()
    root.mainloop()
    return accepted[0] if accepted else None
