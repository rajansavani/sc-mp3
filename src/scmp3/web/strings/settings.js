// Text for the options menu and first-run setup.
export default {
  title: "Settings",
  backToQueue: "Back to the queue",
  sectionsLabel: "Settings sections",
  sections: {
    library: "Library",
    titles: "Title style",
    audio: "Audio and cover art",
    workflow: "Workflow",
    account: "SoundCloud account",
    engine: "Engine and updates",
    help: "Spotify help",
  },

  // Fields, by the setting each one changes.
  labels: {
    library_dir: "Folder",
    filename_template: "File name",
    when_file_exists: "When a file with that name exists",
    title_style: "Featured artists go",
    custom_title_template: "Title template",
    custom_artist_template: "Artist template",
    artist_separator: "Between artist names",
    feat_wording: "feat. wording",
    remove_producer_credits: "Producer credits",
    remove_promo_text: "Promo text",
    singles_album: "Album for singles",
    singles_album_text: "Album name for singles",
    custom_words: "Words to always remove from titles",
    audio_format: "Format",
    cover_max_size: "Largest cover size",
    comment_mode: "Comment tag",
    concurrent_downloads: "Downloads at once",
  },
  hints: {
    feat_wording: "Used by “feat. in the title”.",
    remove_producer_credits: "Like “[prod. Hazy]”.",
    remove_promo_text: "Like “FREE DL”.",
    audio_format: "Spotify on phones only plays MP3 local files.",
    cover_max_size: "Smaller covers are never enlarged.",
  },
  // The label of each choice, by its value as text, since object keys are always text.
  choices: {
    when_file_exists: {
      ask: "Ask me",
      keep_both: "Keep both",
      replace: "Replace it",
      skip: "Skip the new one",
    },
    title_style: {
      artist_field: "In the artist field",
      feat_in_title: "feat. in the title",
      all_in_title: "All artists in the title",
      custom: "Custom",
    },
    artist_separator: { ", ": "Kaia, Lou V", " & ": "Kaia & Lou V", " x ": "Kaia x Lou V" },
    feat_wording: { "feat.": "feat.", "ft.": "ft.", featuring: "featuring", with: "with" },
    remove_producer_credits: { true: "Remove", false: "Keep" },
    remove_promo_text: { true: "Remove", false: "Keep" },
    singles_album: { title: "Same as the title", fixed: "Fixed text", blank: "Leave blank" },
    audio_format: { mp3_320: "MP3 320 kbps", mp3_v0: "MP3 V0", m4a: "Keep AAC as .m4a" },
    cover_max_size: { 300: "300 px", 640: "640 px", 1000: "1000 px", 1400: "1400 px" },
    comment_mode: {
      source_url: "The source link",
      description: "The source's description",
      none: "Nothing",
    },
    concurrent_downloads: { 1: "1", 2: "2", 3: "3", 4: "4" },
  },
  switches: {
    artist_folders: {
      label: "Put each artist in their own folder",
      hint: "Only affects tracks you save from now on.",
    },
    album_subfolders: {
      label: "Put albums in their own folders",
      hint: "Singles don't get an album folder.",
    },
    keep_lossless_originals: {
      label: "Also keep lossless originals",
      hint: "WAV and FLAC files go to a separate folder.",
    },
    save_cover_jpg: { label: "Also save cover.jpg next to the file", hint: "" },
    review_before_saving: {
      label: "Always review before saving",
      hint: "Turn off to save with the suggested tags.",
    },
    download_while_reviewing: {
      label: "Download while I review",
      hint: "Saving is then almost instant.",
    },
    offer_clipboard_links: {
      label: "Offer links from the clipboard",
      hint: "Asks before adding anything.",
    },
  },
  change: "Change",
  customWordsPlaceholder: "One per line, for example: [Bass Boosted]",
  previewLabel: "Preview:",
  fileNameTokens: "You can use {artist}, {title}, {album}, {album_artist}, {year} and {track}",
  templateTokens: "You can use {title}, {version}, {main}, {featured} and {artists}.",
  titlePreview: (title, artist) => (artist ? `Preview: ${title} by ${artist}` : `Preview: ${title}`),

  // Why a setting wasn't saved, by the reason code app/bridge/settings.py sends.
  fieldErrors: {
    UNKNOWN_SETTING: () => "sc-mp3 doesn't have this setting.",
    INVALID_VALUE: () => "sc-mp3 can't use this value. Choose another one.",
    NOT_ABSOLUTE: () => "Choose a folder by its full path.",
    NOT_A_FOLDER: ({ folder }) => `${folder} is a file, not a folder. Choose a folder.`,
    CANT_CREATE: ({ folder }) =>
      `sc-mp3 can't create ${folder}. Check that the drive is connected, or choose another folder.`,
    NOT_WRITABLE: ({ folder }) => `sc-mp3 can't save files in ${folder}. Choose another folder.`,
    INSIDE_APP_FOLDERS: () => "Choose a folder outside sc-mp3's own folders.",
    UNKNOWN_TOKEN: ({ token }) => `${token} isn't a token. Check its spelling against the list below.`,
    FEAT_TOKEN: () => "There's no {feat} token. Write the wording yourself, as in (ft. {featured}).",
    OPEN_BRACE: () => "Each token needs both of its braces, as in {title}.",
    NO_TOKENS: () => "Use at least one of the tokens below.",
  },
  // Names a folder in a message when the problem didn't say which one.
  thisFolder: "this folder",
  // Why nothing was saved, by the error kind update_settings sends.
  saveErrors: {
    disk: "Couldn't write your settings to disk, so this change wasn't saved. Check that the disk isn't full, or restart sc-mp3 and try again.",
    internal:
      "This change wasn't saved because sc-mp3 hit an error. Try again, and if it keeps happening, restart sc-mp3.",
  },

  // The placeholders until signing in (T-041) and updates (T-071) are built.
  account: {
    notSignedIn: "Not signed in",
    notBuilt: "Signing in to SoundCloud isn't built yet.",
    signIn: "Sign in to SoundCloud",
  },
  spotifyHelp: {
    title: "Getting your songs into Spotify",
    detail:
      "Adding the folder in Spotify on your computer, getting the songs onto your phone, and what to try when songs don't show up.",
    open: "Spotify help",
  },
  engine: {
    app: (version) => `sc-mp3 ${version}`,
    engine: (version) => (version ? `Download engine: yt-dlp ${version}` : "Download engine: yt-dlp"),
    builtIn: "Built in",
    versionUnknown: "Its version couldn't be read.",
    diagnostics: "Diagnostics",
    diagnosticsDetail:
      "The versions, your settings and the latest log lines, for a bug report. Folders show only their last part, and sign-in details are left out.",
    notBuilt: "Update checks, engine updates and YouTube support aren't built yet.",
  },

  firstRun: {
    setup: "Setup",
    stepsLabel: "Setup steps",
    stepOf: (number, total) => `Step ${number} of ${total}`,
    back: "Back",
    // Each step's label in the step bar, its heading, and its button to go on.
    steps: {
      folder: { label: "Where songs go", heading: "Where your songs go", next: "Next" },
      titles: { label: "How titles look", heading: "How titles look", next: "Next" },
      account: {
        label: "SoundCloud account",
        heading: "SoundCloud account (optional)",
        next: "Skip for now",
        nextSignedIn: "Next",
      },
      spotify: { label: "Connect Spotify", heading: "Add the folder to Spotify", next: "Finish" },
    },
    folderIntro:
      "Each track you save lands in this folder as a tagged MP3. In the last step you add the folder to Spotify, once.",
    folderLabel: "Library folder",
    chooseFolder: "Choose folder",
    folderHint: "You can change it later in Settings.",
    titlesIntro: "Pick where featured artists go. Here is the same SoundCloud title in each style.",
    sampleTitle: "“Midnight Drive (feat. Lou V) [prod. Hazy] FREE DL” · uploaded by Kaia",
    titleStylesLabel: "Title style",
    cardTitle: "Title",
    cardArtist: "Artist",
    titlesHint: "You can change this in Settings, or for a single track while you review it.",
    accountIntro:
      "Signing in is optional. It gets you the artist's original file when they allow downloads, and better audio with Go+. You can do it later from the top bar.",
    benefitsCorner: "What you get",
    benefitsColumns: ["Not signed in", "Free account", "Go+"],
    benefits: [
      { what: "Stream quality", values: ["AAC 160 kbps", "AAC 160 kbps", "AAC 256 kbps"] },
      {
        what: "The artist's original file, when they allow downloads",
        values: ["No", "Yes", "Yes"],
      },
      { what: "Go+ only tracks", values: ["30-second preview", "30-second preview", "Full track"] },
      { what: "Tracks a label has copy-protected", values: ["No", "No", "No"] },
    ],
    signIn: "Sign in to SoundCloud",
    passwordTip: "Signed up with Google or Apple? Set a SoundCloud password first.",
    spotifyIntro:
      "Spotify plays your saved songs once it knows the folder. Do this once in Spotify on your computer.",
    // The steps in Spotify's desktop app on each system, given the library folder's short name.
    spotifySteps: {
      windows: (folder) => [
        "Open Spotify, select your profile picture at the top right, then Settings.",
        "Under Your Library, turn on Show Local Files.",
        `Choose Add a source, then pick ${folder}.`,
      ],
      mac: (folder) => [
        "Open Spotify, then choose Spotify › Settings in the menu bar.",
        "Under Your Library, turn on Show Local Files.",
        `Choose Add a source, then pick ${folder}.`,
      ],
      linux: (folder) => [
        "Open Spotify, select your profile picture at the top right, then Settings.",
        "Under Your Library, turn on Show Local Files.",
        `Choose Add a source, then pick ${folder}.`,
      ],
    },
    openSpotify: "Open Spotify",
    phoneTip: "Want the songs on your phone too? Settings › Spotify help shows how.",
  },
};
