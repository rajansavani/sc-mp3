// Text for SoundCloud sets: the queue item, the set editor and its track table.

// A count as people read it, such as 2,000.
const number = (count) => count.toLocaleString("en-US");
const tracks = (count) => (count === 1 ? "1 track" : `${number(count)} tracks`);

export default {
  tracks,

  // The queue item's status line.
  status: {
    listing: "Fetching the set's tracks…",
    fetching: (done, total) => `Fetching track info · ${number(done)} of ${number(total)}`,
    downloading: (pct) => `Needs review · ${pct}%`,
    someFailed: (count) => `Needs review · ${number(count)} failed`,
    saving: (saved, total) => `Saving · ${number(saved)} of ${number(total)}`,
    saved: (count) => `Saved · ${tracks(count)}`,
  },

  // The editor while the set is still being listed.
  listingTitle: "Fetching the set's tracks…",
  listingBody: "Each track's title, artist and status show up here as its info comes in.",

  // The editor's header and the mode switch.
  eyebrow: (count) => `SoundCloud set · ${tracks(count)}`,
  saveAs: "Save as",
  modes: { album: "Album", singles: "Singles" },
  modeNotes: {
    album: "One album with a shared cover and track numbers.",
    singles: "Each track saves on its own, with its own cover and no track number.",
  },
  // What SoundCloud calls the set, by its set type, which picks the mode it opens in.
  setTypes: {
    album: "SoundCloud marks this set as an album.",
    ep: "SoundCloud marks this set as an EP.",
    single: "SoundCloud marks this set as a single.",
    compilation: "SoundCloud marks this set as a compilation.",
    playlist: "SoundCloud lists this set as a playlist.",
  },
  albumSettled: "The album's fields, cover and tracks are fixed now that a track is saved.",
  albumWaiting: (count) =>
    count === 1
      ? "The album saves once every track's info is in, and 1 track doesn't have its info yet. To save without it, leave it out."
      : `The album saves once every track's info is in, and ${number(count)} tracks don't have theirs yet. To save without them, leave them out.`,

  // The track table.
  tracksTitle: "Tracks",
  tracksHint: "Edit a title in place, or select an artist to change who's credited. Cleanups shows what was cleaned up in each title.",
  columns: {
    save: "Save",
    number: "#",
    title: "Title",
    artist: "Artist",
    cleanups: "Cleanups",
    status: "Status",
  },
  includeLabel: (title) => `Save ${title}`,
  titleLabel: (title) => `Title of ${title}`,
  cleanups: (count) => (count === 0 ? "No cleanups" : count === 1 ? "1 cleanup" : `${count} cleanups`),
  cleanupsLabel: (title) => `Cleanups in ${title}`,
  untitled: (number) => `Track ${number}`,
  // Why a track can never be saved, by its error kind, which is also why its box is off.
  finalReasons: {
    preview_only: "Only a 30-second preview, so it can't be saved",
    drm: "Copy-protected by its label, so it can't be saved",
    unavailable: "Private or removed, so it can't be saved",
    geo_blocked: "Not available where you are, so it can't be saved",
  },
  trackStatus: {
    queued: "Waiting",
    resolving: "Fetching info…",
    savesOnceIn: "Saves once its info is in",
    ready: "Ready",
    downloading: (pct) => `Downloading · ${pct}%`,
    leftOut: "Left out",
    duplicate: "Already in your library",
    nameTaken: "A file with this name is already there",
    needsMainArtist: "Needs a main artist before it saves",
    savesWhenDone: (pct) => `Downloading · ${pct}% · saves when done`,
    waitsForAlbum: "Saves once every track's info is in",
    saving: "Saving…",
    saved: "Saved",
    skipped: "Skipped",
  },

  // Saving.
  savesTo: (folder) => `Saves to ${folder}, one file per track`,
  skipSet: "Skip set",
  saveAll: (count) => `Save all ${tracks(count)}`,
  saveSelected: (count) => `Save ${number(count)} selected`,
  noneSelected: "No tracks to save",
  savingNow: (saved, total) => `Saving ${number(saved)} of ${tracks(total)}…`,
  retryFailed: (count) => (count === 1 ? "Retry the failed track" : `Retry ${number(count)} failed tracks`),
  savedTitle: (count, folder) => `Saved ${tracks(count)} to ${folder}`,
  savedWithFailures: (count) =>
    count === 1 ? "1 track wasn't saved. Its row says why." : `${number(count)} tracks weren't saved. Their rows say why.`,
};
