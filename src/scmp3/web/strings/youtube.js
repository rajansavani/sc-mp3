// Text for the YouTube support dialog: the question, the download's progress and how it ended.
export default {
  askTitle: "Add YouTube support?",
  askBody:
    "Saving from YouTube needs one extra part, about 40 MB, downloaded once. SoundCloud works without it.",
  askDetail:
    "The part is Deno, a small program the download engine uses to read YouTube pages. It comes from Deno's official releases on GitHub, is checked against its published checksum, and is kept with sc-mp3's other data.",
  notNow: "Not now",
  download: "Download it",

  downloadingTitle: "Adding YouTube support",
  progressLabel: "Download progress",
  // The sizes are in megabytes, rounded to whole ones.
  progress: (done, total) => `${done} of ${total} MB · then a quick check`,
  progressNoTotal: (done) => `${done} MB so far · then a quick check`,
  starting: "Starting the download…",
  checking: "Downloaded. Checking it now…",
  cancel: "Cancel",

  readyTitle: "YouTube support is ready",
  // Which links that waited for it are fetching their info now, and once that's done, that
  // they're in the queue.
  readyOne: (title) => `“${title}” is fetching its info now.`,
  readyMany: (count) => `${count} YouTube links are fetching their info now.`,
  arrivedOne: (title) => `“${title}” is in the queue.`,
  arrivedMany: (count) => `${count} YouTube links are in the queue.`,
  readyNone: "You can add YouTube links now.",
  done: "Done",

  failedTitle: "Couldn't add YouTube support",
  // Why the install stopped, by the error kind in the components event.
  failed: {
    offline: "The download stopped before it finished. Check your connection and try again.",
    bad_download: "The download didn't match its published checksum, so it wasn't installed. Try again.",
    disk: "sc-mp3 couldn't write to its data folder. Check that the drive has free space, then try again.",
    rate_limited: "GitHub asked sc-mp3 to wait. Try again in a few minutes.",
    service_down: "GitHub isn't responding right now. Try again in a few minutes.",
    // No build for this computer's system, or a download link that no longer works.
    unavailable: "Deno's download for this computer couldn't be found. Try again later, or update sc-mp3.",
    other:
      "sc-mp3 hit an unexpected error while adding YouTube support. Try again, and if it happens again, copy the diagnostics from Settings and include them in a bug report.",
  },
  soundcloudStillWorks: "SoundCloud links keep working.",
  tryAgain: "Try again",
};
