// Text for a job that stopped with an error: each kind's queue status, headline and detail,
// and the buttons its banner offers. main.js takes these in, as strings.main.errors and so on.

// Linux installs ffmpeg from the distro, since sc-mp3 ships its own only on Windows and macOS.
// Each command has a line to itself with nothing after it, so it can be copied as it is.
const INSTALL_FFMPEG = [
  "Install it, then retry.",
  "Ubuntu and Debian: sudo apt install ffmpeg",
  "Fedora: sudo dnf install ffmpeg-free",
  "Arch: sudo pacman -S ffmpeg",
].join("\n");

export default {
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
      // YouTube's reasons, as errorVariant() names them.
      variants: {
        private: {
          status: "Private video",
          headline: () => "This video is private.",
          detail: () => "Only people the uploader shares it with can watch it, so sc-mp3 can't save it.",
        },
        age_restricted: {
          status: "Age-restricted",
          headline: () => "YouTube shows this video only to signed-in adults.",
          detail: () => "sc-mp3 doesn't sign in to YouTube, so it can't save this video. Try another upload of the song.",
        },
        members_only: {
          status: "Members only",
          headline: () => "This video is only for the channel's members.",
          detail: () => "sc-mp3 can't save it. Try another upload of the song.",
        },
        paid: {
          status: "Paid video",
          headline: () => "YouTube shows this video only to people who paid for it.",
          detail: () => "sc-mp3 can't save it. Try another upload of the song.",
        },
        removed: {
          status: "Not available",
          headline: () => "This video isn't available. It may have been removed.",
          detail: () => "Check the link, or try another upload of the song.",
        },
        live_stream: {
          status: "Live stream",
          headline: () => "This live stream hasn't ended yet.",
          detail: () => "Retry once it's over and YouTube has the recording.",
        },
      },
    },
    geo_blocked: {
      status: "Not available here",
      headline: () => "This track isn't available in your country.",
    },
    rate_limited: {
      status: "Waiting to retry",
      headline: ({ source }) => `${source} asked us to slow down.`,
      // The time comes from Python, which waits longer each time the limit comes back.
      detail: ({ retryAt }) => (retryAt ? `Trying again at ${retryAt}.` : "Trying again in a minute."),
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
      variants: {
        unreadable_file: {
          status: "Not MP3 or M4A audio",
          headline: () => "This file isn't MP3 or M4A audio that sc-mp3 can read.",
          detail: () => "It may be damaged, or have the wrong extension.",
        },
        ffmpeg_missing: {
          status: "ffmpeg not found",
          headline: ({ os }) =>
            os === "linux"
              ? "sc-mp3 needs ffmpeg to convert the audio, and it isn't installed."
              : "sc-mp3 can't find its copy of ffmpeg, which it needs to convert the audio.",
          detail: ({ os }) => (os === "linux" ? INSTALL_FFMPEG : "Reinstall sc-mp3, then retry."),
        },
        tag_write_failed: {
          status: "Couldn't write the tags",
          headline: () => "sc-mp3 couldn't write the new tags into this file.",
          detail: () => "The file is unchanged. Retry, and if it fails again, the file may be damaged.",
        },
      },
    },
    disk: {
      status: "Couldn't save",
      headline: ({ folder }) => `Can't save to ${folder}.`,
      detail: () => "Check that the drive is connected, or choose another folder.",
      // By the error's reason, or the folder it was about when that isn't the library, as
      // errorVariant() names them.
      variants: {
        data: {
          headline: () => "sc-mp3 couldn't write to its own data folder.",
          detail: () => "Check that the drive has free space, then retry.",
        },
        file_in_use: {
          status: "File in use",
          headline: () => "The file is open in another program or is read-only.",
          detail: () => "Close it and try again.",
        },
        path_too_long: {
          status: "Folder path too long",
          headline: () => "The folder path is too long for this file.",
          detail: () => "Choose a folder closer to the top of the drive.",
        },
        // A re-tag that couldn't write into the file, and a file that's gone.
        file: {
          headline: () => "sc-mp3 couldn't write the new tags into the file.",
          detail: () => "The file is unchanged. Check that its drive is connected and has free space, then retry.",
        },
        file_locked: {
          status: "Couldn't read the file",
          headline: () => "sc-mp3 couldn't read this file.",
          detail: () => "Close any program that's using it, then try again.",
        },
        file_changed: {
          status: "Changed elsewhere",
          headline: () => "Another program changed this file since sc-mp3 read it, so it wasn't overwritten.",
          detail: () => "Reload it to start from what's in it now.",
        },
        file_missing: {
          status: "File not found",
          headline: () => "The file isn't there anymore.",
          detail: () => "Check that it wasn't moved or renamed, and that its drive is connected. Then retry.",
        },
      },
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
    youtube_support_missing: {
      status: "Needs YouTube support",
      headline: () => "YouTube links need a one-time download of about 40 MB.",
      detail: () => "SoundCloud links work without it.",
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
    reload_file: "Reload the file",
    show_it: "Show it",
    save_again: "Save again",
    add_youtube_support: "Add YouTube support",
  },
};
