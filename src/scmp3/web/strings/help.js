// Text for the Spotify help panel, from the design's A-spotify-help artboard.
export default {
  title: "Spotify help",
  tabsLabel: "Help topics",
  tabs: {
    desktop: "On your computer",
    phone: "On your phone",
    stale: "Songs not showing up",
  },

  desktopIntro:
    "Spotify on your computer plays the songs in your sc-mp3 folder once it knows about it. You do this once.",
  // The steps, given the library folder's short name, such as "Music › sc-mp3".
  desktopSteps: (folder) => [
    "Open Spotify and go to Settings.",
    "Under Your Library, turn on Show Local Files.",
    `Choose Add a source, then pick ${folder}. Your songs appear under Local Files in Your Library.`,
  ],
  openSpotify: "Open Spotify",

  phoneIntro:
    "Spotify on a phone plays MP3s stored on the phone itself. You don't need Premium or Wi-Fi sync.",
  // Local audio files goes on first, since that order works whether or not the iPhone's Spotify
  // folder exists yet.
  phoneSections: [
    {
      title: "First, in Spotify on the phone",
      text: "Go to Settings and privacy › Apps and devices and turn on Local audio files.",
    },
    {
      title: "iPhone",
      text: "Then copy the MP3s into Files › On My iPhone › Spotify, for example with AirDrop or from your computer with Finder or iTunes file sharing. Copy the song files themselves, not the artist folders they're in, since Spotify on iPhone works best with every file in that one folder.",
    },
    {
      title: "Android",
      text: "Then copy the MP3s into your phone's storage, for example the Music folder, over USB.",
    },
  ],
  phoneNote: "Spotify's old sync from your computer to your phone over Wi-Fi is no longer supported.",

  staleIntro:
    "Spotify keeps its own list of local files and can be slow to notice new or edited ones. Try these in order.",
  staleSteps: [
    "In Spotify's settings, turn Show Local Files off, then on again.",
    "Remove the sc-mp3 folder from Spotify's sources, then add it again.",
    "Quit Spotify, delete the file local-files.bnk, then open Spotify again. It rebuilds the list.",
  ],
  openLocalFiles: "Open the folder with local-files.bnk",
  // Where the button looks, on each system.
  localFilesHint: {
    windows: "sc-mp3 finds the right folder for the Spotify installer version and the Microsoft Store version.",
    mac: "sc-mp3 finds the folder in your Library folder.",
    linux: "sc-mp3 finds the folder in your config folder.",
  },
};
