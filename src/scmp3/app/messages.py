"""Text that Python shows itself, outside the web UI. Everything the UI shows is in web/strings.

Python sends the UI codes, never text, so error kinds and states have no text here.
"""

# Shown in a message box when a second copy of the app is started.
ALREADY_OPEN_TITLE = "sc-mp3"
ALREADY_OPEN = "sc-mp3 is already open."

# Asked when the window is closed with tracks in the queue that haven't been saved, since
# closing removes them.
UNSAVED_TITLE = "sc-mp3"


def unsaved(count: int) -> str:
    """The question for closing while `count` tracks in the queue haven't been saved."""
    if count == 1:
        return "1 track hasn't been saved. Close anyway?\n\nClosing removes it from the queue."
    return (
        f"{count} tracks haven't been saved. Close anyway?\n\nClosing removes them from the queue."
    )


# The file type filters in the native file dialogs. pywebview takes "Name (*.ext;*.ext)", with
# only letters, digits and spaces in the name.
AUDIO_FILES = "MP3 and M4A files (*.mp3;*.m4a)"
IMAGE_FILES = "Images (*.jpg;*.jpeg;*.png;*.webp;*.gif;*.bmp;*.tif;*.tiff;*.avif)"
