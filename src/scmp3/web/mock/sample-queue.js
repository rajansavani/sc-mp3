// The sample queue from the design, and builders for jobs in the shape of Job.to_dict() in
// core/models.py, with the same snake_case keys.
import { horizon, nightRoad, PALETTES, videoFrame } from "./sample-art.js";

let nextId = 1;

export const AAC_160 = {
  format_id: "hls_aac_160k",
  ext: "m4a",
  codec: "aac",
  bitrate: 160,
  is_preview: false,
  is_original: false,
};

export const OPUS_160 = {
  format_id: "251",
  ext: "webm",
  codec: "opus",
  bitrate: 160,
  is_preview: false,
  is_original: false,
};

export function makeJob(fields) {
  return {
    id: `job-${nextId++}`,
    kind: "track",
    input: "",
    state: "queued",
    source: null,
    suggestion: null,
    overrides: {},
    style: null,
    artwork: { kind: "source", value: null, crop: null },
    staged_audio: null,
    output_path: null,
    error: null,
    progress: null,
    duplicate: null,
    children: [],
    album_mode: null,
    included: true,
    // What T-022's bridge adds: a taken file name to ask about, a save without its cover, and
    // why the last cover picked couldn't be used.
    collision: null,
    cover_missing: false,
    cover_problem: null,
    ...fields,
  };
}

export function makeSource(fields) {
  return {
    kind: "soundcloud",
    id: "",
    url: "",
    title: "",
    uploader: "",
    artists: [],
    album: null,
    release_date: null,
    upload_date: null,
    genre: null,
    description: null,
    duration: null,
    full_duration: null,
    artwork_url: null,
    artwork_is_avatar: false,
    isrc: null,
    label: null,
    explicit: null,
    availability: "ok",
    original: null,
    formats: [AAC_160],
    playlist: null,
    ...fields,
  };
}

export function makeSuggestion(fields) {
  return {
    base_title: "",
    main_artists: [],
    featured: [],
    version: null,
    album: null,
    album_artist: null,
    year: null,
    genre: null,
    track_no: null,
    track_total: null,
    changes: [],
    ...fields,
  };
}

export function makeChange(id, kind, span, label, enabled = true) {
  return { id, kind, span, label, enabled };
}

function midnightDrive() {
  const url = "https://soundcloud.com/kaia/midnight-drive";
  return makeJob({
    id: "midnight-drive",
    input: url,
    state: "needs_review",
    source: makeSource({
      id: "soundcloud:tracks:1001",
      url,
      title: "Midnight Drive (feat. Lou V) [prod. Hazy] FREE DL",
      uploader: "Kaia",
      genre: "Hip-hop",
      upload_date: "2026-03-14",
      duration: 221,
      artwork_url: nightRoad(1000),
    }),
    suggestion: makeSuggestion({
      base_title: "Midnight Drive",
      main_artists: ["Kaia"],
      featured: ["Lou V"],
      album: "Midnight Drive",
      year: 2026,
      genre: "Hip-hop",
      track_no: 1,
      changes: [
        makeChange("feat", "extract_feature", "(feat. Lou V)", "Lou V credited as featured artist"),
        makeChange("prod", "remove_producer", "[prod. Hazy]", "Removed “[prod. Hazy]”"),
        makeChange("freedl", "remove_promo", "FREE DL", "Removed “FREE DL”"),
      ],
    }),
    staged_audio: "staging/midnight-drive/audio.m4a",
  });
}

function lanternTrack(title, index) {
  const url = `https://soundcloud.com/mara-lune/${title.toLowerCase().replaceAll(" ", "-")}`;
  return makeJob({
    id: `lantern-${index + 1}`,
    input: url,
    state: "needs_review",
    source: makeSource({
      id: `soundcloud:tracks:${2001 + index}`,
      url,
      title,
      uploader: "Mara Lune",
      duration: 180 + index * 17,
    }),
    suggestion: makeSuggestion({
      base_title: title,
      main_artists: ["Mara Lune"],
      album: "Lantern",
      album_artist: "Mara Lune",
      year: 2026,
      track_no: index + 1,
      track_total: 5,
    }),
  });
}

// The design's set: one track downloads, one is a Go+ preview that can't be saved, and one has a
// promo tag cleaned up.
function lanternTracks() {
  const titles = ["Lantern", "Paper Boats (feat. Ilo)", "Undertow", "Glass Hours", "Low Tide (VIP Mix)"];
  const children = titles.map(lanternTrack);
  const [, boats, undertow, glass] = children;
  boats.suggestion = makeSuggestion({
    ...boats.suggestion,
    base_title: "Paper Boats",
    featured: ["Ilo"],
    changes: [makeChange("feat", "extract_feature", "(feat. Ilo)", "Ilo credited as featured artist")],
  });
  undertow.progress = { pct: 40, speed: 1450000, eta: 12, downloaded: null, total: null };
  Object.assign(glass, {
    state: "error",
    error: { kind: "preview_only", detail: null, retry_at: null },
    suggestion: null,
  });
  return children;
}

function lantern() {
  const url = "https://soundcloud.com/mara-lune/sets/lantern";
  const children = lanternTracks();
  const entries = children.map((child) => ({
    url: child.source.url,
    id: child.source.id,
    title: child.source.title,
  }));
  return makeJob({
    id: "lantern",
    kind: "set",
    input: url,
    state: "needs_review",
    source: makeSource({
      id: "soundcloud:playlists:3001",
      url,
      title: "Lantern",
      uploader: "Mara Lune",
      formats: [],
      artwork_url: horizon(PALETTES.lantern),
      playlist: {
        title: "Lantern",
        owner: "Mara Lune",
        set_type: "album",
        is_album: true,
        artwork_url: horizon(PALETTES.lantern),
        entries,
      },
    }),
    children,
  });
}

function staticBloom() {
  // Still fetching info, so there's no source yet. The mock fills it in a few seconds later.
  return makeJob({
    id: "static-bloom",
    input: "https://www.youtube.com/watch?v=q3StatBloom",
    state: "resolving",
  });
}

// What "Static Bloom" turns out to be once its info arrives: a YouTube video with a wide cover.
export function staticBloomInfo() {
  return {
    source: makeSource({
      kind: "youtube",
      id: "youtube:q3StatBloom",
      url: "https://www.youtube.com/watch?v=q3StatBloom",
      title: "Juno Vale - Static Bloom (Live Session) [Official Video]",
      uploader: "Juno Vale",
      upload_date: "2026-05-02",
      artwork_url: videoFrame(1280, 720),
      formats: [OPUS_160],
    }),
    suggestion: makeSuggestion({
      base_title: "Static Bloom",
      version: " (Live Session)",
      main_artists: ["Juno Vale"],
      album: "Static Bloom (Live Session)",
      year: 2026,
      changes: [
        makeChange(
          "artist",
          "strip_redundant_artist",
          "Juno Vale",
          "Removed the artist name “Juno Vale” from the title",
        ),
        makeChange("video", "remove_promo", "[Official Video]", "Removed “[Official Video]”"),
      ],
    }),
  };
}

function nightBus() {
  const url = "https://soundcloud.com/teo-marr/night-bus";
  return makeJob({
    id: "night-bus",
    input: url,
    // Save was pressed before the download finished, so it saves when the download is done.
    state: "saving",
    source: makeSource({
      id: "soundcloud:tracks:4001",
      url,
      title: "Night Bus",
      uploader: "Teo Marr",
      artwork_url: horizon(PALETTES.nightBus),
    }),
    suggestion: makeSuggestion({
      base_title: "Night Bus",
      main_artists: ["Teo Marr"],
      album: "Night Bus",
    }),
    progress: { pct: 62, speed: 1450000, eta: 4, downloaded: 5400000, total: 8700000 },
  });
}

function oldFavorite() {
  const path = "~/Music/old-favorite.mp3";
  return makeJob({
    id: "old-favorite",
    kind: "retag",
    input: path,
    state: "saved",
    source: makeSource({
      kind: "file",
      id: path,
      url: path,
      title: "old-favorite.mp3",
      formats: [],
      artwork_url: horizon(PALETTES.oldFavorite),
    }),
    // The file had no tags, so there's no title to suggest and the queue shows the file name.
    suggestion: makeSuggestion({ base_title: "", main_artists: [] }),
    output_path: path,
  });
}

// The five items the design shows, in its order.
export function sampleQueue() {
  return [midnightDrive(), lantern(), staticBloom(), nightBus(), oldFavorite()];
}

const MANY_PALETTES = [PALETTES.dusk, PALETTES.tide, PALETTES.moss, PALETTES.nightBus];

// A batch of tracks waiting for review with their audio in, for ?mock&many=<count>.
export function manyTracks(count) {
  return Array.from({ length: count }, (_, index) => {
    const number = index + 1;
    const url = `https://soundcloud.com/teo-marr/batch-track-${number}`;
    return makeJob({
      id: `batch-${number}`,
      input: url,
      state: "needs_review",
      source: makeSource({
        id: `soundcloud:tracks:${5000 + number}`,
        url,
        title: `Batch Track ${number} [FREE DL]`,
        uploader: "Teo Marr",
        duration: 200,
        artwork_url: horizon(MANY_PALETTES[index % MANY_PALETTES.length], 640),
      }),
      suggestion: makeSuggestion({
        base_title: `Batch Track ${number}`,
        main_artists: ["Teo Marr"],
        album: `Batch Track ${number}`,
        changes: [makeChange("freedl", "remove_promo", "[FREE DL]", "Removed “[FREE DL]”")],
      }),
      staged_audio: `staging/batch-${number}/audio.m4a`,
    });
  });
}

export const SAMPLE_HISTORY = [
  {
    id: 1,
    source_id: null,
    url_norm: null,
    saved_path: "~/Music/old-favorite.mp3",
    title: "Old Favorite",
    artist: "Unknown artist",
    album: "Old Favorite",
    format: "mp3",
    bitrate: 320,
    saved_at: "2026-10-03T18:20:00",
  },
];
