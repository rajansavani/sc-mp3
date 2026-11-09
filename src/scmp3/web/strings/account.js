// Text for signing in to SoundCloud: the dialog, the top bar's account chip, and what went wrong.
export default {
  signInTitle: "Sign in to SoundCloud",
  accountTitle: "SoundCloud account",
  intro:
    "Optional. It gets you the artist's original file when they allow downloads, and AAC 256 kbps with Go+.",
  waysLabel: "Ways to sign in",
  // The dialog never names the browser. Only Settings lists them.
  ways: { window: "Sign in with your browser", browser: "Copy from your browser" },

  window: {
    intro:
      "SoundCloud's sign-in page opens in a new browser window. Sign in there any way you like, with Google, Apple or your email.",
    open: "Continue in your browser",
    googleNote: "If your browser asks whether soundcloud.com may use Google sign-in, select Allow.",
    note: "The window has a profile of its own, so your usual browser isn't touched. sc-mp3 keeps that profile so Google remembers you next time, and deletes it when you sign out.",
    waitingTitle: "Continue in your browser",
    // Where the window can see the sign-in, it closes by itself, and the button is the backup.
    waiting: {
      closesItself:
        "Sign in on the SoundCloud page that opened. The window closes by itself once you're signed in. If it stays open, select I've signed in.",
      byHand:
        "When SoundCloud shows you're signed in, select I've signed in. Closing the browser window yourself works too.",
    },
    finish: "I've signed in",
    finishing: "Reading your sign-in. If the browser window is still open, close it yourself.",
    cancel: "Cancel without signing in",
    tryAgain: "Try again",
    useBrowser: "Copy from your browser",
  },

  browser: {
    intro: "Works with any account, including Google and Apple. In Chrome or Edge:",
    steps: [
      "Open a private window with Ctrl+Shift+N and sign in at soundcloud.com.",
      "Press F12, open Application, then Cookies, then https://soundcloud.com.",
      ["Copy the value of ", "oauth_token", " into the first box below."],
      ["Copy the value of ", "oauth_refresh_token", ", the row just above it, into the second."],
      "Close the private window without signing out.",
    ],
    screenshotAlt:
      "Edge's developer tools on the Application tab, with Cookies and https://soundcloud.com open. The oauth_refresh_token row sits just above the oauth_token row.",
    accessLabel: "oauth_token",
    refreshLabel: "oauth_refresh_token",
    submit: "Sign in",
    checking: "Checking with SoundCloud",
  },

  footer: (keyringName) =>
    `sc-mp3 renews the sign-in on its own, so you stay signed in for months. It's stored in ${keyringName}, and Settings has Sign out.`,
  footerFile:
    "sc-mp3 renews the sign-in on its own, so you stay signed in for months. Settings has Sign out.",

  // Where the sign-in is kept: the system's keychain, by the OS, or sc-mp3's own file.
  keyringNames: {
    windows: "Windows Credential Manager",
    mac: "the macOS Keychain",
    linux: "your system's keyring",
  },
  storedInKeyring: (keyringName) => `Stays signed in on its own. Stored in ${keyringName}.`,
  storedInFile:
    "Stays signed in on its own. Stored in a local file, since this system's keyring isn't available.",

  // The plans, as the top bar's chip names them, and as Settings and the dialog do.
  plansShort: { free: "Free", go: "Go", go_plus: "Go+", dj: "DJ" },
  plans: { free: "Free account", go: "Go", go_plus: "Go+", dj: "DJ" },
  chip: (handle, plan) => `${handle} · ${plan}`,
  chipUnnamed: "Signed in",
  chipEnded: "Sign in again",
  chipEndedLabel: "Your SoundCloud sign-in ended. Sign in again.",
  signedInAs: (handle, plan) => `Signed in as ${handle} · ${plan}`,
  signedInUnnamed: "Signed in to SoundCloud",
  signedInToast: (handle) => `Signed in to SoundCloud as ${handle}.`,
  ended: "Your SoundCloud sign-in ended. Sign in again.",
  signIn: "Sign in to SoundCloud",
  signInAgain: "Sign in again",
  signOut: "Sign out",
  signingOut: "Signing out",

  // A pasted value that can't be the token its box asks for, by TokenProblem.
  refused: {
    access_empty: "Paste the oauth_token value here.",
    refresh_empty: "Paste the oauth_refresh_token value here.",
    access_is_refresh:
      "This looks like oauth_refresh_token. Paste the oauth_token value here, the long one that starts with eyJ.",
    refresh_is_access:
      "This looks like oauth_token. Paste the oauth_refresh_token value here, the shorter one.",
    access_not_token:
      "This isn't an oauth_token value. Copy the whole value of the oauth_token row, which starts with eyJ.",
    refresh_not_token:
      "This isn't an oauth_refresh_token value. Copy the value of the oauth_refresh_token row, just above oauth_token.",
  },
  // Why signing in or out didn't work, by error kind.
  problems: {
    auth_expired:
      "SoundCloud didn't accept this sign-in. Sign in again in a new private window, copy both values, and try again.",
    offline: "Couldn't reach SoundCloud. Check your internet connection, then try again.",
    service_down: "SoundCloud isn't answering right now. Try again in a few minutes.",
    rate_limited: "SoundCloud asked sc-mp3 to slow down. Try again in a few minutes.",
    engine_outdated:
      "SoundCloud answered in a way sc-mp3 doesn't understand. Update the download engine in Settings, then try again.",
    disk: "Couldn't save the sign-in on this computer. Check that the disk isn't full, then try again.",
    other:
      "sc-mp3 hit an error while signing in. Try again, and if it keeps happening, restart sc-mp3.",
  },
  // Why the sign-in window gave no sign-in, by problem or error kind.
  windowProblems: {
    no_sign_in:
      "The browser window closed before SoundCloud signed you in, so there's no sign-in to use. Try again, and stay in the window until SoundCloud shows you're signed in.",
    timed_out:
      "sc-mp3 closed the browser window after 10 minutes without a sign-in. Try again when you're ready.",
    no_browser:
      "sc-mp3 couldn't find a browser to open the sign-in in. Use Copy from your browser instead.",
    browser_unknown:
      "sc-mp3 couldn't tell whether the browser window is still open. Close it if it is, then try again, or use Copy from your browser.",
    auth_expired: "SoundCloud didn't accept the sign-in. Try again.",
    // The window's own cookies had a shape sc-mp3 refuses, so SoundCloud changed something.
    refused:
      "SoundCloud's sign-in didn't look the way sc-mp3 expects, so it wasn't used. Check for a newer version of sc-mp3, then try again.",
    offline: "Couldn't reach SoundCloud. Check your internet connection, then try again.",
    service_down: "SoundCloud isn't answering right now. Try again in a few minutes.",
    rate_limited: "SoundCloud asked sc-mp3 to slow down. Try again in a few minutes.",
    engine_outdated:
      "SoundCloud answered in a way sc-mp3 doesn't understand. Update the download engine in Settings, then try again.",
    disk: "Couldn't save the sign-in on this computer. Check that the disk isn't full, then try again.",
    other:
      "The browser didn't start, or sc-mp3 couldn't read the sign-in from it. Try again, or use Copy from your browser.",
  },
  signOutProblems: {
    disk: "Couldn't delete the sign-in from this computer. Restart sc-mp3, then try again.",
    profile_left:
      "You're signed out, but sc-mp3 couldn't delete the browser profile it keeps for signing in. Close any browser window sc-mp3 opened, then select Sign out again.",
    other:
      "sc-mp3 hit an error while signing out. Try again, and if it keeps happening, restart sc-mp3.",
  },
  // Shown with Sign out when no sign-in is showing, but something of one may still be kept.
  keptNote:
    "sc-mp3 may still keep a browser profile from signing in. Sign out deletes it, and anything else a sign-in left.",
};
