// Text for the shell around the screens: dialogs, toasts and starting up.
export default {
  close: "Close",
  back: "Back",
  dismiss: "Dismiss",
  notificationsLabel: "Notifications",
  startFailed: "sc-mp3 couldn't load your queue and settings. Close the window and open it again.",
  // What each error kind a call returns means. Job errors get their own text later.
  errors: {
    not_implemented: "That part of sc-mp3 isn't built yet.",
  },
  unknownError: (kind) =>
    `sc-mp3 hit an error it has no message for (${kind}). Copy the diagnostics from Settings and include them in a bug report.`,
};
