// Extra sample jobs for ?mock&states: the warnings, a save waiting for its download, a taken file
// name, a cover that couldn't be used, a save without its cover, and one job for each error kind,
// so every banner can be seen without making anything fail.
import { artistFolder } from "./names.js";
import {
  avatar,
  horizon,
  LETTERBOXED_CROP,
  letterboxedFrame,
  nightRoad,
  PALETTES,
} from "./sample-art.js";
import { makeChange, makeJob, makeSource, makeSuggestion } from "./sample-queue.js";

let nextSample = 1;

// Downloads that stay where they are, so their state can be looked at for as long as needed.
export const FROZEN_DOWNLOADS = new Set(["sample-harbor-lights"]);

// Where a save puts the track, in its artist's folder, as artist folders are on by default.
function libraryPath(artist, title) {
  return `~/Music/sc-mp3/${artistFolder(artist)}/${artist} - ${title}.mp3`;
}

function track({
  title,
  artist,
  state = "needs_review",
  kind = "soundcloud",
  art = PALETTES.dusk,
  ...fields
}) {
  const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const url =
    kind === "youtube"
      ? `https://www.youtube.com/watch?v=sample${nextSample++}`
      : `https://soundcloud.com/${artist.toLowerCase().replaceAll(" ", "-")}/${slug}`;
  return makeJob({
    id: `sample-${slug}`,
    input: url,
    state,
    source: makeSource({
      kind,
      id: `${kind}:sample:${slug}`,
      url,
      title,
      uploader: artist,
      artwork_url: horizon(art),
      ...fields.source,
    }),
    suggestion: makeSuggestion({ base_title: title, main_artists: [artist], album: title, year: 2026 }),
    staged_audio: `staging/${slug}/audio.m4a`,
    ...fields.job,
  });
}

// The review-states screen: saved before, with a 300 × 300 cover.
function savedBefore() {
  const job = track({
    title: "Midnight Drive",
    artist: "Kaia",
    source: {
      title: "Midnight Drive (feat. Lou V) [prod. Hazy] FREE DL",
      url: "https://soundcloud.com/kaia/midnight-drive-again",
      artwork_url: nightRoad(300),
    },
  });
  job.suggestion = makeSuggestion({
    base_title: "Midnight Drive",
    main_artists: ["Kaia"],
    featured: ["Lou V"],
    album: "Midnight Drive",
    year: 2026,
    genre: "Hip-hop",
    track_no: 1,
    changes: [
      makeChange("feat", "extract_feature", "(feat. Lou V)", "Lou V credited as featured artist"),
    ],
  });
  job.duplicate = {
    id: 2,
    source_id: job.source.id,
    url_norm: job.source.url,
    saved_path: libraryPath("Kaia", "Midnight Drive"),
    title: "Midnight Drive",
    artist: "Kaia",
    album: "Midnight Drive",
    format: "mp3",
    bitrate: 320,
    saved_at: "2026-10-03T18:20:00",
  };
  return job;
}

// SoundCloud gave the uploader's avatar, because the track has no cover of its own.
function avatarCover() {
  return track({
    title: "Paper Moon",
    artist: "Kaia",
    source: { artwork_url: avatar(500), artwork_is_avatar: true },
  });
}

// Save was pressed early, and the download stays at 40% so the state can be looked at.
function waitingForDownload() {
  return track({
    title: "Harbor Lights",
    artist: "Teo Marr",
    state: "saving",
    art: PALETTES.tide,
    job: {
      staged_audio: null,
      progress: { pct: 40, speed: 900000, eta: 9, downloaded: 3500000, total: 8700000 },
    },
  });
}

// Saving found a file with the same name, and the setting says to ask.
function nameTaken() {
  return track({
    title: "Low Tide",
    artist: "Ilo",
    art: PALETTES.tide,
    job: { collision: { path: libraryPath("Ilo", "Low Tide") } },
  });
}

// The image picked as the new cover couldn't be used, so the old one stays.
function unusableCover() {
  return track({ title: "Morning Train", artist: "Kaia", job: { cover_problem: "unreadable" } });
}

// Saved, but the cover couldn't be downloaded in time.
function savedWithoutCover() {
  return track({
    title: "Lighthouse",
    artist: "Juno Vale",
    state: "saved",
    job: {
      staged_audio: null,
      output_path: libraryPath("Juno Vale", "Lighthouse"),
      cover_missing: true,
    },
  });
}

// An older YouTube video without an HD thumbnail: its cover is cut from the picture between the
// black bands.
function letterboxedCover() {
  return track({
    title: "Old Session",
    artist: "Mara Lune",
    kind: "youtube",
    source: { artwork_url: letterboxedFrame() },
    job: { default_crop: LETTERBOXED_CROP },
  });
}

// One job per error kind. `reviewed` ones failed while saving, so their review stays up.
// `where` and `reason` say more about a disk error, as T-022's bridge does.
const ERROR_SAMPLES = [
  { kind: "drm", title: "Golden Hour", artist: "Ilo" },
  { kind: "preview_only", title: "Glass Hours", artist: "Mara Lune" },
  { kind: "unavailable", title: "Quiet Rooms", artist: "Ilo" },
  { kind: "geo_blocked", title: "Coastline", artist: "Juno Vale" },
  { kind: "rate_limited", title: "Ember Light", artist: "Teo Marr" },
  { kind: "auth_expired", title: "Afterglow", artist: "Kaia" },
  { kind: "engine_outdated", title: "Slow Motion", artist: "Teo Marr" },
  { kind: "bot_check", title: "Static Bloom (Live Session)", artist: "Juno Vale", source: "youtube" },
  { kind: "offline", title: "Northbound", artist: "Mara Lune" },
  { kind: "service_down", title: "Satellite", artist: "Ilo", source: "youtube" },
  { kind: "internal", title: "Paper Boats", artist: "Mara Lune" },
  { kind: "bad_download", title: "Velvet", artist: "Juno Vale", source: "youtube" },
  { kind: "youtube_support_missing", title: "Paper Kites", artist: "Ilo", source: "youtube" },
  { kind: "unavailable", title: "Night Swim", artist: "Kaia", source: "youtube", reason: "private" },
  { kind: "unavailable", title: "Red Room", artist: "Teo Marr", source: "youtube", reason: "age_restricted" },
  { kind: "unavailable", title: "Backstage", artist: "Mara Lune", source: "youtube", reason: "members_only" },
  { kind: "unavailable", title: "Box Office", artist: "Teo Marr", source: "youtube", reason: "paid" },
  { kind: "unavailable", title: "Lost Tape", artist: "Ilo", source: "youtube", reason: "removed" },
  { kind: "unavailable", title: "Sunset Set", artist: "Juno Vale", source: "youtube", reason: "live_stream" },
  { kind: "disk", title: "Daybreak", artist: "Kaia", reviewed: true },
  { kind: "disk", title: "Undertow", artist: "Ilo", reviewed: true, where: "data" },
  { kind: "disk", title: "Window Seat", artist: "Mara Lune", reviewed: true, reason: "file_in_use" },
  { kind: "disk", title: "Long Way Down", artist: "Teo Marr", reviewed: true, reason: "path_too_long" },
  { kind: "convert_failed", title: "Tidal", artist: "Teo Marr", reviewed: true },
  { kind: "duplicate", title: "Old Light", artist: "Ilo", reviewed: true },
];

function failed({ kind, title, artist, source = "soundcloud", reviewed = false, ...more }) {
  const job = track({ title, artist, kind: source, state: "error", art: PALETTES.moss });
  // A rate limit's next try is a few minutes off, as after its second wait.
  const retryAt = kind === "rate_limited" ? new Date(Date.now() + 120000).toISOString() : null;
  job.error = {
    kind,
    detail: null,
    where: more.where ?? null,
    reason: more.reason ?? null,
    retry_at: retryAt,
  };
  if (!reviewed) {
    job.suggestion = null;
    job.staged_audio = null;
  }
  if (kind === "duplicate") {
    job.duplicate = { ...savedBefore().duplicate, saved_path: libraryPath(artist, title) };
  }
  return job;
}

export function sampleStates() {
  return [
    savedBefore(),
    avatarCover(),
    waitingForDownload(),
    nameTaken(),
    unusableCover(),
    savedWithoutCover(),
    letterboxedCover(),
    ...ERROR_SAMPLES.map(failed),
  ];
}
