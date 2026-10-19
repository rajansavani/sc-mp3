// Text for the main window: the top bar, the queue and the editors.
export default {
  appName: "sc-mp3",
  pasteLabel: "Add links",
  pastePlaceholder: "Paste SoundCloud or YouTube links, or drop audio files to re-tag",
  add: "Add",
  signIn: "Sign in to SoundCloud",
  settings: "Settings",
  queueLabel: "Queue",
  queueTitle: (count) => `Queue · ${count}`,
  queueEmpty: "Nothing in the queue yet.",
  editorEmpty: "Paste a SoundCloud or YouTube link above to start.",
  states: {
    queued: "Waiting",
    resolving: "Fetching info…",
    needs_review: "Needs review",
    saving: "Saving…",
    saved: "Saved",
    skipped: "Skipped",
    error: "Couldn't save",
  },
  downloading: (pct) => `Downloading · ${Math.round(pct)}%`,
  coverLabel: "Cover art",
  setTracks: (count) => `${count} tracks`,
  // Why an added item was rejected, by the reason code add_inputs returns. The codes are the
  // constant names in core/urls.py and core/inputs.py.
  rejectReasons: {
    NOT_SUPPORTED: "Not a supported link. Paste a SoundCloud or YouTube link, or add an MP3 or M4A file.",
    SOUNDCLOUD_NOT_TRACK: "This SoundCloud link isn't a track or a set. Open the track or set and copy its link.",
    SHORT_LINK_DEAD_END:
      "This SoundCloud short link doesn't lead to a track or a set. Open it in a browser and copy the full link.",
    YOUTUBE_PLAYLIST: "YouTube playlists aren't supported yet.",
    YOUTUBE_NOT_VIDEO: "This YouTube link isn't a video. Open the video and copy its link.",
    FILE_NOT_FOUND: "File not found. Check the path.",
    FOLDER_NOT_FILE: "Folders can't be added. Add the MP3 or M4A files inside the folder instead.",
    NOT_RETAGGABLE: "Only MP3 and M4A files can be re-tagged.",
    PASTE_TOO_LONG:
      "Only the first 2,000 links and files, up to 1,000,000 characters, were read. Add the rest in another paste.",
  },
};
