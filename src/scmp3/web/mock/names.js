// File and folder names made safe the way core/filenames.py does, closely enough for the mock.

export function safeName(name) {
  return name
    .replace(/[\\/:*?"<>|]/g, "-")
    .replace(/\s+/g, " ")
    .replace(/^[ .-]+|[ .-]+$/g, "");
}

// Artist folders are capped at 100 characters, as in core/library.py.
export function artistFolder(albumArtist) {
  const capped = safeName(albumArtist).slice(0, 100);
  // The cut can leave a space or a dot at the end, so it's trimmed again.
  return safeName(capped) || "Unknown artist";
}
