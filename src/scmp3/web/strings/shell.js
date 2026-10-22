// Text for the shell around the screens: dialogs, toasts and starting up.
export default {
  close: "Close",
  back: "Back",
  dismiss: "Dismiss",
  notificationsLabel: "Notifications",
  // The page shown when the window couldn't start.
  startFailedTitle: "sc-mp3's window couldn't start",
  startFailed:
    "Close sc-mp3 and open it again. If this keeps happening, copy the diagnostics and include them in a bug report.",
  copyDiagnostics: "Copy diagnostics",
  diagnosticsCopied: "The diagnostics are on your clipboard. Paste them into your bug report.",
  diagnosticsNotCopied: "The diagnostics couldn't be copied. Mention that in your bug report instead.",
  // What each error kind a call returns means. Job errors get their own text later.
  errors: {
    not_implemented: "That part of sc-mp3 isn't built yet.",
    unknown_job: "That item isn't in the queue anymore.",
    not_editable: "This item is saving or done, so it can't be changed now.",
    invalid_artwork: "That cover can't be used. Choose an image file, or a link that starts with https://.",
    file_missing: "That file isn't there anymore. It may have been moved or deleted.",
    spotify_missing: "Spotify didn't open. Check that the Spotify app is installed, then try again.",
    spotify_folder_missing:
      "sc-mp3 couldn't find Spotify's folder on this computer. Open Spotify once, then try again.",
    internal:
      "sc-mp3 hit an unexpected error. Try again, and if it keeps happening, copy the diagnostics from Settings and include them in a bug report.",
  },
  unknownError: (kind) =>
    `sc-mp3 hit an error it has no message for (${kind}). Copy the diagnostics from Settings and include them in a bug report.`,
};
