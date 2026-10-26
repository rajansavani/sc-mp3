// The mock's queue and how it moves: fetching info, downloads, saves, failures and retries, in
// the order T-022's job manager runs them (TECH_SPEC §3 and §15).
// A link ending in /error-<kind>, such as https://soundcloud.com/x/error-drm, fails with that
// kind, while fetching info or while saving.
import { forTheUi } from "./review.js";
import { horizon, PALETTES, videoFrame } from "./sample-art.js";
import {
  makeSource,
  makeSuggestion,
  manyTracks,
  OPUS_160,
  SAMPLE_HISTORY,
  sampleQueue,
  staticBloomInfo,
} from "./sample-queue.js";
import { FROZEN_DOWNLOADS, sampleStates } from "./sample-states.js";
import { snapshotPart as settingsPart } from "./settings.js";
import { hasYouTubeSupport, whenYouTubeSupportArrives } from "./youtube.js";

// How long each simulated step takes, such as fetching info or saving.
const STEP_MS = 700;
// How far a download gets in each step, and the speed it reports, in bytes a second.
const DOWNLOAD_STEP_PCT = 3;
const DOWNLOAD_SPEED = 1450000;
// When the design's "Static Bloom" finishes fetching its info.
const STATIC_BLOOM_STEPS = 14;
// The automatic retries the error messages promise, in milliseconds.
const AUTO_RETRY_MS = { offline: 30000, rate_limited: 60000 };

const AUDIO_FILE = /\.(?:mp3|m4a)$/i;
const ERROR_LINK = /\/error-([a-z_]+)$/;
// Errors that happen while saving, after the review. The others happen while fetching info.
const SAVE_ERRORS = new Set(["disk", "convert_failed", "duplicate"]);

const query = new URLSearchParams(window.location.search);
const showsStates = query.has("states");
// ?mock&many=<count> adds that many tracks in review, for trying a long queue.
const manyCount = Number.parseInt(query.get("many") ?? "0", 10) || 0;
export const jobs = [
  ...sampleQueue(),
  ...(showsStates ? sampleStates() : []),
  ...manyTracks(manyCount),
];
// Jobs whose download is running, so a cancel or a removal can stop it.
const downloading = new Set();

// Copied on the way out, like everything that crosses the real bridge.
export function emit(event) {
  window.scmp3.emit(JSON.parse(JSON.stringify(event)));
}

export function findJob(jobId) {
  for (const job of jobs) {
    if (job.id === jobId) return job;
    const child = job.children.find((candidate) => candidate.id === jobId);
    if (child) return child;
  }
  return null;
}

function ownerOf(job) {
  return jobs.find((top) => top === job || top.children.includes(job)) ?? null;
}

// Events carry whole queue items, so a set's track goes out as its whole set.
export function publish(job) {
  const owner = ownerOf(job);
  if (owner) emit({ type: "job", job: forTheUi(owner) });
}

function later(steps, change) {
  setTimeout(change, steps * STEP_MS);
}

// The last part of the link or path stands in for the title a source would give.
function titleFromInput(input) {
  const last = input.split(/[/\\?=]/).filter(Boolean).at(-1) ?? input;
  const words = last.replace(AUDIO_FILE, "").replaceAll("-", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function sourceKind(job) {
  if (job.kind === "retag") return "file";
  return /youtu/i.test(job.input) ? "youtube" : "soundcloud";
}

function failingKind(job) {
  return ERROR_LINK.exec(job.input)?.[1] ?? null;
}

function scheduleAutoRetry(job) {
  const kind = job.error?.kind;
  const wait = AUTO_RETRY_MS[kind];
  if (!wait) return;
  setTimeout(() => {
    // Removing the job, or any retry in between, stops this one.
    if (!ownerOf(job) || job.state !== "error" || job.error?.kind !== kind) return;
    retryJob(job);
    publish(job);
  }, wait);
}

function fail(job, kind) {
  job.state = "error";
  const wait = AUTO_RETRY_MS[kind];
  const retryAt = wait ? new Date(Date.now() + wait).toISOString() : null;
  job.error = { kind, detail: null, retry_at: retryAt };
  job.progress = null;
  downloading.delete(job.id);
  if (kind === "duplicate") {
    const savedPath = `~/Music/sc-mp3/${job.input.split("/").at(-1)}.mp3`;
    job.duplicate = { ...SAMPLE_HISTORY[0], saved_path: savedPath };
  }
  scheduleAutoRetry(job);
}

function newSource(job) {
  const kind = sourceKind(job);
  const title = titleFromInput(job.input);
  const fields = { kind, id: `mock:${job.id}`, url: job.input, title, uploader: "Sample Artist" };
  if (kind === "file") return makeSource({ ...fields, formats: [] });
  // YouTube's covers are wide video frames, which the cover crops to the middle square.
  if (kind === "youtube") {
    return makeSource({ ...fields, artwork_url: videoFrame(1280, 720), formats: [OPUS_160] });
  }
  return makeSource({ ...fields, artwork_url: horizon(PALETTES.dusk) });
}

// Sample jobs whose info is written out already, by id.
const SAMPLE_INFO = { "static-bloom": staticBloomInfo };

// A retried job keeps the source it already had. A YouTube link waits for YouTube support.
function resolve(job) {
  if (sourceKind(job) === "youtube" && !job.source && !hasYouTubeSupport()) {
    fail(job, "youtube_support_missing");
    return;
  }
  const sample = SAMPLE_INFO[job.id];
  if (sample && !job.source) {
    Object.assign(job, sample(), { state: "needs_review" });
    startDownload(job);
    return;
  }
  job.source = job.source ?? newSource(job);
  const failure = failingKind(job);
  if (failure && !SAVE_ERRORS.has(failure)) {
    fail(job, failure);
    return;
  }
  const { title, uploader } = job.source;
  job.state = "needs_review";
  job.suggestion = makeSuggestion({ base_title: title, main_artists: [uploader], album: title });
  if (job.source.kind !== "file") startDownload(job);
  // Save was pressed on its set while its info was still coming.
  if (job.saves_once_in) {
    job.saves_once_in = false;
    startSaving(job);
  }
}

export function fetchInfoLater(job) {
  later(1, () => {
    job.state = "resolving";
    publish(job);
  });
  later(3, () => {
    if (!ownerOf(job)) return;
    resolve(job);
    publish(job);
  });
}

function finishSaving(job) {
  const failure = failingKind(job);
  if (failure && SAVE_ERRORS.has(failure)) {
    fail(job, failure);
    return;
  }
  job.state = "saved";
  job.progress = null;
  job.error = null;
  const folder = settingsPart().settings.library_dir;
  job.output_path = job.kind === "retag" ? job.input : `${folder}/${viewOf(job).filename}`;
}

// The job as the UI gets it: a set's track as part of its set, at its place in the album.
function viewOf(job) {
  const owner = ownerOf(job);
  if (!owner || owner === job) return forTheUi(job);
  return forTheUi(owner).children.find((child) => child.id === job.id);
}

function saveLater(job) {
  later(2, () => {
    if (job.state !== "saving") return;
    finishSaving(job);
    publish(job);
  });
}

function tickDownload(job) {
  if (!downloading.has(job.id) || !ownerOf(job)) return;
  const pct = Math.min(100, (job.progress?.pct ?? 0) + DOWNLOAD_STEP_PCT);
  if (pct < 100) {
    const eta = Math.ceil((100 - pct) / DOWNLOAD_STEP_PCT);
    job.progress = { ...job.progress, pct, speed: DOWNLOAD_SPEED, eta };
    emit({ type: "progress", job_id: job.id, pct, speed: DOWNLOAD_SPEED, eta });
    later(1, () => tickDownload(job));
    return;
  }
  downloading.delete(job.id);
  job.progress = null;
  job.staged_audio = `staging/${job.id}/audio.m4a`;
  publish(job);
  if (job.state === "saving") saveLater(job);
}

function startDownload(job) {
  if (downloading.has(job.id) || FROZEN_DOWNLOADS.has(job.id)) return;
  downloading.add(job.id);
  job.progress = job.progress ?? { pct: 0, speed: null, eta: null, downloaded: null, total: null };
  later(1, () => tickDownload(job));
}

export function stopDownload(job) {
  downloading.delete(job.id);
  job.progress = null;
}

// Without its audio yet, the job waits for the download and saves when it's done.
export function startSaving(job) {
  job.state = "saving";
  job.error = null;
  if (job.staged_audio || job.kind === "retag") saveLater(job);
  else startDownload(job);
}

// Where a failed job was going: fetching its info, saving it, or converting its audio.
function failedStep(job) {
  if (!job.suggestion) return "resolve";
  return job.error?.kind === "convert_failed" ? "convert" : "save";
}

const RETRIES = {
  resolve: (job) => {
    job.state = "queued";
    fetchInfoLater(job);
  },
  save: (job) => startSaving(job),
  // The staged audio may be what failed, so it's downloaded again.
  convert: (job) => {
    job.staged_audio = null;
    startSaving(job);
  },
};

// Runs the failed step again and carries on to where the job was going.
export function retryJob(job) {
  const step = failedStep(job);
  job.error = null;
  RETRIES[step](job);
}

// The sample queue moves the way a real one would: Night Bus finishes downloading and saves,
// Static Bloom's info arrives, and the waiting errors retry on their own.
function startSampleActivity() {
  for (const job of jobs) {
    if (job.progress && !job.staged_audio) startDownload(job);
    if (job.state === "error") scheduleAutoRetry(job);
  }
  const bloom = jobs.find((job) => job.id === "static-bloom");
  if (!bloom) return;
  later(STATIC_BLOOM_STEPS, () => {
    if (!ownerOf(bloom) || bloom.state !== "resolving") return;
    if (!hasYouTubeSupport()) {
      fail(bloom, "youtube_support_missing");
      publish(bloom);
      return;
    }
    Object.assign(bloom, staticBloomInfo(), { state: "needs_review" });
    startDownload(bloom);
    publish(bloom);
  });
}

// Once YouTube support is in, the links that waited for it fetch their info, as in Python.
whenYouTubeSupportArrives(() => {
  for (const job of jobs) {
    if (job.state !== "error" || job.error?.kind !== "youtube_support_missing") continue;
    retryJob(job);
    publish(job);
  }
});

startSampleActivity();
