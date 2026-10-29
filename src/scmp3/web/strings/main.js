// Text for the main window: the top bar, the queue, the track editor and the cover.
import jobErrors from "./errors.js";

// A count as people read it, such as 2,000.
const number = (count) => count.toLocaleString("en-US");
// An artist at the end of a title comes with the dash before it, as "- Kaia", which isn't shown.
const withoutSeparators = (span) => span.replace(/^[\s|/:-]+|[\s|/:-]+$/g, "");

export default {
  appName: "sc-mp3",
  queueLabel: "Queue",

  // The top bar and the clipboard offer under it.
  pasteLabel: "Add links",
  pastePlaceholder: "Paste SoundCloud or YouTube links, or drop audio files to re-tag",
  add: "Add",
  signIn: "Sign in to SoundCloud",
  settings: "Settings",
  clipboardOffer: "Add the link on your clipboard?",
  clipboardNotNow: "Not now",
  clipboardAdd: "Add it",
  // What a paste or drop of more than one item added, in a toast.
  addedMany: (count) => `Added ${number(count)} items to the queue.`,
  addedOne: "Added 1 item to the queue.",
  notAddedOne: (input, reason) => `${input} wasn't added. ${reason}`,
  notAddedMany: (count, reason) => `${number(count)} items weren't added. ${reason}`,
  alreadyQueuedMany: (count) => `${number(count)} of these are already in the queue.`,

  // Why an added item was rejected, by the reason code add_inputs returns. The codes are the
  // constant names in core/urls.py and core/inputs.py.
  rejectReasons: {
    NOT_SUPPORTED:
      "Not a link sc-mp3 can save. Paste a SoundCloud or YouTube link, or add an MP3 or M4A file to re-tag.",
    SOUNDCLOUD_NOT_TRACK: "This SoundCloud link isn't a track or a set. Open the track or set and copy its link.",
    SHORT_LINK_DEAD_END:
      "This SoundCloud short link doesn't lead to a track. Open it in a browser and copy the track's link.",
    YOUTUBE_PLAYLIST: "YouTube playlists can't be saved yet. Paste the link of one video instead.",
    YOUTUBE_NOT_VIDEO: "This YouTube link isn't a video. Open the video and copy its link.",
    FILE_NOT_FOUND: "File not found. Check the path.",
    FOLDER_NOT_FILE: "Folders can't be added. Add the MP3 or M4A files inside the folder instead.",
    NOT_RETAGGABLE: "Only MP3 and M4A files can be re-tagged.",
    PASTE_TOO_LONG:
      "Only the first 2,000 links, up to 1,000,000 characters, were read. Add the rest in another paste.",
    ALREADY_IN_QUEUE: "Already in the queue.",
    // Only a short link reaches the UI with this code, since the queue takes set links.
    SETS_NOT_SUPPORTED: "This short link leads to a SoundCloud set. Open it in a browser and paste the set's own link instead.",
    // From the drop handler: two dropped files with the same name could get each other's paths.
    SAME_NAME_DROP: "Two of the dropped files have the same name, so sc-mp3 can't tell them apart. Add them one at a time.",
  },

  // The queue.
  queueTitle: (count) => `Queue · ${number(count)}`,
  clearFinished: "Clear finished",
  queueEmpty: "Nothing queued yet. Links you add show up here, and unsaved ones stay after you quit.",
  // Saving every item that waits for review, after a question.
  saveAll: "Save all",
  saveAllTitle: (count) => `Save ${number(count)} tracks to your library?`,
  saveAllBody:
    "Each track is saved with the title, artist and cover it has now. Tracks still downloading are saved when their download finishes.",
  saveAllConfirm: (count) => `Save ${number(count)} tracks`,
  sources: { soundcloud: "SoundCloud", youtube: "YouTube", file: "File" },
  itemLine: (artist, source) => (artist ? `${artist} · ${source}` : source),
  setLine: (owner, tracks) => (owner ? `${owner} · SoundCloud set · ${tracks}` : "SoundCloud set"),
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
  openAudioFiles: "Open audio files",
  setsToo: "Sets and albums work too",
  pasteKeys: { mac: "⌘V", other: "Ctrl+V" },
  pasteHint: "anywhere adds the links on your clipboard",
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
    artist_from_title: (span) => `Kept “${withoutSeparators(span)}” in the title`,
    strip_redundant_artist: (span) => `Kept “${withoutSeparators(span)}” in the title`,
    fix_caps: (span) => `Kept “${span}” as written`,
    remove_promo: (span) => `Kept “${span}”`,
    remove_producer: (span) => `Kept “${span}”`,
    credit_producer: (span) => `Left the producer ${span} out of the artists`,
    remove_emoji: (span) => `Kept “${span}”`,
    custom_word: (span) => `Kept “${span}”`,
  },
  outputLabel: "Output",
  savesAs: "Saves as",
  tagsEmbedded: "Tags and cover embedded",
  tagsWithoutCover: "Tags embedded, no cover",
  retagAudio: "Only the tags change. The audio stays as it is.",
  // The audio line, such as "AAC 160 kbps stream → MP3 320 kbps".
  audioStream: (codec, bitrate) => (bitrate ? `${codec} ${bitrate} kbps stream` : `${codec} stream`),
  audioOriginal: (format) => `Original ${format}`,
  audioConverted: (from, to) => `${from} → ${to}`,
  audioCopied: (from) => `${from}, copied without re-encoding`,
  codecNames: { aac: "AAC", mp3: "MP3", opus: "Opus", vorbis: "Vorbis", flac: "FLAC", wav: "WAV", alac: "ALAC" },
  audioTargets: { mp3_320: "MP3 320 kbps", mp3_v0: "MP3 V0", m4a: "AAC in .m4a" },

  // Re-tagging a file you already have.
  retagTitle: "Re-tagging a file you already have",
  retagBody: "Nothing is downloaded. Your changes are written into this file.",
  titleFromFileName: (name) => `The file has no title tag, so the title comes from its name, “${name}”.`,
  playlistTitle: "Is this song in a Spotify playlist?",
  playlistBody:
    "Changing its title, artist or album makes Spotify treat it as a different song. It drops out of those playlists, so you'd add it back afterwards.",
  playlistDontAsk: "Don't ask again",
  playlistSave: "Save anyway",
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
  savedWithoutCover: "Saved without a cover: the cover couldn't be downloaded.",
  retagSavedTitle: (name) => `Tags updated in ${name}`,
  retagSavedHint: "Spotify can take a moment to notice. If it doesn't, the Spotify help has three things to try.",
  retagSavedWithoutCover: "The new cover couldn't be used, so the file keeps its old one.",
  showInFolder: "Show in folder",
  openSpotify: "Open Spotify",
  backToReview: "Back to review",
  skippedNote: "Skipped. Nothing was saved, and the downloaded audio was deleted.",

  // The question when a file with the same name is already there, under the "Ask me" setting.
  collisionTitle: "A file with this name is already in your library",
  collisionBody:
    "Replace the file that's there, keep both, or skip this track. Keeping both adds a number to the new file's name.",
  collisionReplace: "Replace",
  collisionKeepBoth: "Keep both",
  collisionSkip: "Skip this track",
  collisionRestOfSet: "Use this answer for the rest of the set",

  // The duplicate warning, from the history of saved files.
  duplicateTitle: (date) => `Already in your library, saved ${date}`,
  duplicateTitleUndated: "Already in your library",

  // Job errors and their buttons, from errors.js.
  ...jobErrors,

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
  // A re-tagged file's own cover, untouched, goes back into the file exactly as it was.
  coverKept: (from) => `${from} · kept as it is`,
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
