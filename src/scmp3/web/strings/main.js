// Text for the main window: the top bar, the queue, the track editor and the cover.
export default {
  appName: "sc-mp3",
  queueLabel: "Queue",
  setTracks: (count) => `${count} tracks`,

  // The top bar and the clipboard offer under it.
  pasteLabel: "Add links",
  pastePlaceholder: "Paste SoundCloud or YouTube links, or drop audio files to re-tag",
  add: "Add",
  signIn: "Sign in to SoundCloud",
  settings: "Settings",
  clipboardOffer: "Add the link on your clipboard?",
  clipboardNotNow: "Not now",
  clipboardAdd: "Add it",
  notAddedOne: (input, reason) => `${input} wasn't added. ${reason}`,
  notAddedMany: (count, reason) => `${count} items weren't added. ${reason}`,
  alreadyQueuedMany: (count) => `${count} of these are already in the queue.`,
  // TODO(T-025): Remove this once a Python drop handler reads the files' full paths.
  dropFilesNotYet: "Dropped files can't be added yet. Use Open audio files instead.",

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
    ALREADY_IN_QUEUE: "Already in the queue.",
    SETS_NOT_SUPPORTED: "SoundCloud sets and albums can't be saved yet. Add each track's own link.",
    YOUTUBE_NOT_SUPPORTED: "YouTube links can't be saved yet.",
    FILES_NOT_SUPPORTED: "Fixing the tags of files you already have isn't possible yet.",
  },

  // The queue.
  queueTitle: (count) => `Queue · ${count}`,
  clearFinished: "Clear finished",
  queueEmpty: "Nothing queued yet. Links you add show up here, and unsaved ones stay after you quit.",
  sources: { soundcloud: "SoundCloud", youtube: "YouTube", file: "File" },
  itemLine: (artist, source) => (artist ? `${artist} · ${source}` : source),
  setLine: (owner, count) => `${owner} · SoundCloud set · ${count} tracks`,
  retagLine: "Re-tag existing file",
  status: {
    queued: "Waiting",
    resolving: "Fetching info…",
    needsReview: "Needs review",
    audioReady: "Needs review · audio ready",
    downloadingForReview: (pct) => `Needs review · ${pct}%`,
    savesWhenDone: "Downloading · saves when done",
    saving: "Saving…",
    saved: "Saved",
    retagSaved: "Saved · tags updated",
    skipped: "Skipped",
  },
  // What the live region says when items need review, are saved or stop with an error.
  announce: (title, status) => `${title}: ${status}`,
  announceMany: {
    needs_review: (count) => `${count} items need review`,
    saved: (count) => `${count} items saved`,
    error: (count) => `${count} items stopped with an error`,
  },

  // The editor with nothing selected.
  emptyTitle: "Paste a SoundCloud or YouTube link to start",
  emptyBody:
    "You check the title, artist and cover, then it saves as an MP3 ready for Spotify. To fix the tags of songs you already have, drop the MP3 or M4A files here.",
  pasteKeys: { mac: "⌘V", other: "Ctrl+V" },
  pasteHint: "anywhere adds the links on your clipboard",
  setsToo: "Sets and albums work too",
  openAudioFiles: "Open audio files",
  spotifyHelp: "Spotify help",

  // The editor while info is still coming.
  queuedTitle: "Waiting to fetch info",
  fetchingTitle: "Fetching info…",
  fetchingBody: "The title, artist and cover show up here in a moment.",
  removeFromQueue: "Remove from the queue",

  // The review form.
  reviewTitle: "Review before saving",
  waiting: (count) => `${count} waiting`,
  fields: {
    title: "Title",
    artist: "Artist",
    album: "Album",
    year: "Year",
    genre: "Genre",
    track_no: "Track #",
    album_artist: "Album artist",
  },
  resetToSuggestion: "Reset to suggestion",
  reset: "Reset",
  moreFields: "More fields",
  fewerFields: "Fewer fields",
  // Why update_fields refused a value, by the reason code it sends.
  fieldErrors: {
    NOT_A_YEAR: "Use a year from 1 to 9999.",
    NOT_A_TRACK: "Use a track number from 1 to 999, such as 3 or 3/12.",
    TOTAL_BEFORE_TRACK: "The total can't be smaller than the track number.",
    other: "This value can't be used. Check it and try again.",
  },
  styleLegend: "Featured artists go",
  styles: {
    artist_field: "In the artist field",
    feat_in_title: "feat. in title",
    all_in_title: "All artists in title",
    custom: "Your template",
  },
  cleanupsTitle: {
    soundcloud: "Cleaned up from the SoundCloud title",
    youtube: "Cleaned up from the YouTube title",
    file: "Cleaned up from the file's title",
  },
  cleanupsHint: "Click a change to undo it",
  noCleanups: "Nothing needed cleaning up.",
  originalTitle: (title, uploader) => (uploader ? `“${title}” · uploaded by ${uploader}` : `“${title}”`),
  // A cleanup that's switched off, by its kind. The text it acts on goes in the quotes.
  keptLabels: {
    extract_feature: (span) => `Kept “${span}” as text`,
    artist_from_title: (span) => `Kept “${span}” in the title`,
    strip_redundant_artist: (span) => `Kept “${span}” in the title`,
    fix_caps: (span) => `Kept “${span}” as written`,
    remove_promo: (span) => `Kept “${span}”`,
    remove_producer: (span) => `Kept “${span}”`,
    remove_emoji: (span) => `Kept “${span}”`,
    custom_word: (span) => `Kept “${span}”`,
  },
  outputLabel: "Output",
  savesAs: "Saves as",
  tagsEmbedded: "Tags and cover embedded",
  retagAudio: "Only the tags change. The audio stays as it is.",
  // The audio line, such as "AAC 160 kbps stream → MP3 320 kbps".
  audioStream: (codec, bitrate) => (bitrate ? `${codec} ${bitrate} kbps stream` : `${codec} stream`),
  audioOriginal: (format) => `Original ${format}`,
  audioConverted: (from, to) => `${from} → ${to}`,
  audioCopied: (from) => `${from}, copied without re-encoding`,
  codecNames: { aac: "AAC", mp3: "MP3", opus: "Opus", vorbis: "Vorbis", flac: "FLAC", wav: "WAV", alac: "ALAC" },
  audioTargets: { mp3_320: "MP3 320 kbps", mp3_v0: "MP3 V0", m4a: "AAC in .m4a" },

  // Saving.
  skip: "Skip",
  save: "Save to library",
  saveRetag: "Save changes to the file",
  enterKey: "Enter",
  cancel: "Cancel",
  downloading: (pct) => `Downloading · ${pct}%`,
  savesWhenDownloaded: "Saves when the download finishes",
  savingNow: "Tagging and moving into your library…",
  savedTo: (folder) => `Saved to ${folder}`,
  retagSavedTitle: (name) => `Tags updated in ${name}`,
  retagSavedHint: "Spotify can take a moment to notice. If it doesn't, the Spotify help has three things to try.",
  showInFolder: "Show in folder",
  openSpotify: "Open Spotify",
  backToReview: "Back to review",
  skippedNote: "Skipped. Nothing was saved, and the downloaded audio was deleted.",

  // The duplicate warning, from the history of saved files.
  duplicateTitle: (date) => `Already in your library, saved ${date}`,
  duplicateTitleUndated: "Already in your library",

  // Each error kind: the short queue status, the headline and the detail. Some take the source's
  // name, the library folder or the date of the earlier save.
  errors: {
    drm: {
      status: "Copy-protected",
      headline: () => "This track is copy-protected by its label, so it can't be saved.",
      detail: () => "Signing in won't change that. Labels turn this on for some releases.",
    },
    preview_only: {
      status: "Preview only",
      headline: () => "Only a 30-second preview is available: this is a Go+ track.",
      detail: () => "With a Go+ subscription you can save the whole track. Otherwise, remove it.",
    },
    unavailable: {
      status: "Private or removed",
      headline: () => "This track is private or was removed.",
      detail: () => "If you have a private share link, paste that instead.",
    },
    geo_blocked: {
      status: "Not available here",
      headline: () => "This track isn't available in your country.",
    },
    rate_limited: {
      status: "Waiting to retry",
      headline: ({ source }) => `${source} asked us to slow down.`,
      detail: () => "Trying again in a minute.",
    },
    auth_expired: {
      status: "Sign-in expired",
      headline: () => "Your SoundCloud sign-in expired.",
      detail: () => "Sign in again, then retry.",
    },
    engine_outdated: {
      status: "Couldn't read the track",
      headline: ({ source }) => `${source} changed something on their end.`,
      detail: () => "Updating the download engine usually fixes this. It takes a few seconds.",
    },
    bot_check: {
      status: "YouTube bot check",
      headline: () => "YouTube wants to confirm you're not a bot.",
      detail: () => "This usually clears up after a while. Wait a few minutes, then retry.",
    },
    offline: {
      status: "Offline",
      headline: () => "You're offline.",
      detail: () => "sc-mp3 tries again when the connection is back.",
    },
    convert_failed: {
      status: "Couldn't convert",
      headline: () => "sc-mp3 couldn't convert the audio.",
      detail: () => "The download may be damaged. Retry to download and convert it again.",
    },
    disk: {
      status: "Couldn't save",
      headline: ({ folder }) => `Can't save to ${folder}.`,
      detail: () => "Check that the drive is connected, or choose another folder.",
    },
    duplicate: {
      status: "Already saved",
      headline: ({ savedOn }) => (savedOn ? `Already in your library, saved ${savedOn}.` : "Already in your library."),
      detail: () => "Show the saved file, or save this one again.",
    },
    service_down: {
      status: "Not responding",
      headline: ({ source }) => `${source} isn't responding right now.`,
      detail: () => "This usually passes. Retry in a few minutes.",
    },
    internal: {
      status: "Unexpected error",
      headline: () => "sc-mp3 hit an unexpected error with this track.",
      detail: () => "Retry it. If it happens again, copy the diagnostics from Settings and include them in a bug report.",
    },
    bad_download: {
      status: "YouTube support didn't install",
      headline: () => "sc-mp3 couldn't install YouTube support: the download was damaged.",
      detail: () => "Retry to download it again.",
    },
  },
  // An error kind this version has no text for.
  unknownErrorStatus: "Stopped with an error",
  // The buttons the error banners offer, by action.
  errorActions: {
    remove: "Remove from the queue",
    retry: "Retry",
    retry_now: "Retry now",
    update_engine: "Update engine",
    sign_in_go_plus: "Sign in with Go+",
    sign_in_again: "Sign in again",
    choose_folder: "Choose another folder",
    show_it: "Show it",
    save_again: "Save again",
  },

  // The cover.
  coverAlt: (title) => (title ? `Cover art for ${title}` : "Cover art"),
  noCover: "No cover art",
  coverFailed: "The cover couldn't be shown.",
  replace: "Replace",
  crop: "Crop",
  coverFrom: {
    soundcloud: "From SoundCloud",
    youtube: "From YouTube",
    file: "From the file",
    url: "From an image link",
    added: "From an image you added",
  },
  // The sizes use no-break spaces, so "1000 × 1000" never splits over two lines.
  coverCaption: (from, size) => (size ? `${from} · embedded at ${size}\u00a0×\u00a0${size}` : from),
  smallCover: (width, height) =>
    `This cover is only ${width}\u00a0×\u00a0${height}, so it may look soft in Spotify.`,
  avatarCover: (name) =>
    `This is ${name}'s profile picture, not cover art for the track. Replace it if you have the real cover.`,
  dropCover: "Drop the image to use it as the cover",
  replaceTitle: "Replace the cover",
  chooseImage: "Choose an image file",
  imageLink: "Image link",
  imageLinkPlaceholder: "https://…",
  useLink: "Use this link",
  replaceHint: (keys) => `You can also paste an image here with ${keys}, or drop one on the cover.`,
  badImageLink: "Enter a link that starts with https://.",
  useOriginalCover: "Use the original cover",
  notAnImage: "That isn't an image. Use a JPEG, PNG or WebP file.",
  imageTooBig: (megabytes) => `This image is over ${megabytes} MB. Use a smaller one.`,
  imageUnreadable: "That image couldn't be read. Try another file.",
  // Why the last new cover wasn't used, by the cover_problem code Python sends with the job.
  coverProblems: {
    unreadable: "That image can't be used as a cover. Use a JPEG, PNG or WebP image under 20 MB.",
    offline: "You're offline, so the image couldn't be downloaded. Try again once you're back online.",
    rate_limited: "The image's site asked sc-mp3 to slow down. Try again in a minute.",
    service_down: "The image's site isn't responding right now. Try again in a few minutes.",
    unavailable: "That link doesn't lead to an image. Copy the image's own address, or save the image and add the file.",
    disk: "The image file couldn't be read. Check that it's still there, then try again.",
    internal: "sc-mp3 hit an unexpected error with that image. Try again, or use another image.",
  },
  cropTitle: "Crop the cover",
  cropHelp: "Drag the square to move it, or drag a corner to resize it. Arrow keys move it, and + and − resize it.",
  cropArea: "Crop area",
  cropSize: (side) => `${side}\u00a0×\u00a0${side} px`,
  cropReset: "Center it",
  useCrop: "Use this crop",
  close: "Close",
};
