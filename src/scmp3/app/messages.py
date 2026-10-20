"""Text that Python shows itself, outside the web UI. Everything the UI shows is in web/strings.

Python sends the UI codes, never text, so error kinds and states have no text here.
"""

# Shown in a message box when a second copy of the app is started.
ALREADY_OPEN_TITLE = "sc-mp3"
ALREADY_OPEN = "sc-mp3 is already open."

# The file type filters in the native file dialogs. pywebview takes "Name (*.ext;*.ext)", with
# only letters, digits and spaces in the name.
AUDIO_FILES = "MP3 and M4A files (*.mp3;*.m4a)"
IMAGE_FILES = "Images (*.jpg;*.jpeg;*.png;*.webp;*.gif;*.bmp;*.tif;*.tiff;*.avif)"
