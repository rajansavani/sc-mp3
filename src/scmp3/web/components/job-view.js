// How a job reads in the queue and the editor: its title, its status line and its error.
// Jobs carry the keys TECH_SPEC §15 lists for the UI: tags, filename, format, cover_url and
// cover_size.
import { strings } from "../strings/index.js";
import { baseName, folderLabel } from "./paths.js";

const text = strings.main;

// Each error kind's tone, icon and buttons, in the order the error view shows them. The first
// action is the main one when `primary` is set, and Remove from the queue always comes last.
// `variants` replace parts of an entry for an error's reason, or for a disk error in sc-mp3's
// own data folder, as errorVariant() names them.
export const ERROR_KINDS = {
  drm: { tone: "danger", icon: "error", actions: ["remove"] },
  preview_only: {
    tone: "warn",
    icon: "error",
    actions: ["sign_in_go_plus", "remove"],
    primary: true,
  },
  unavailable: { tone: "danger", icon: "error", actions: ["remove"] },
  geo_blocked: { tone: "danger", icon: "error", actions: ["remove"] },
  rate_limited: { tone: "neutral", icon: "clock", actions: ["remove"] },
  auth_expired: {
    tone: "warn",
    icon: "person",
    actions: ["sign_in_again", "retry", "remove"],
    primary: true,
  },
  engine_outdated: {
    tone: "danger",
    icon: "error",
    actions: ["update_engine", "retry", "remove"],
    primary: true,
  },
  bot_check: { tone: "warn", icon: "error", actions: ["retry", "remove"] },
  offline: { tone: "warn", icon: "offline", actions: ["retry_now", "remove"] },
  convert_failed: { tone: "danger", icon: "error", actions: ["retry", "remove"] },
  disk: {
    tone: "danger",
    icon: "folder",
    actions: ["choose_folder", "retry", "remove"],
    variants: {
      // Another library folder wouldn't help with these two, so it isn't offered.
      data: { actions: ["retry", "remove"] },
      file_in_use: { icon: "error", actions: ["retry", "remove"] },
    },
  },
  duplicate: { tone: "warn", icon: "clock", actions: ["show_it", "save_again", "remove"] },
  service_down: { tone: "warn", icon: "error", actions: ["retry", "remove"] },
  internal: { tone: "danger", icon: "error", actions: ["retry", "remove"] },
  bad_download: { tone: "danger", icon: "error", actions: ["retry", "remove"] },
};

// What an unknown error kind gets, so a newer backend never leaves the editor blank.
const UNKNOWN_KIND = { tone: "danger", icon: "error", actions: ["retry", "remove"] };

const LINK_HOSTS = [
  { pattern: /(^|\.)(youtube\.com|youtu\.be)$/i, kind: "youtube" },
  { pattern: /(^|\.)soundcloud\.com$/i, kind: "soundcloud" },
];

const AUDIO_FILE = /\.(mp3|m4a)$/i;

// A link or path without its scheme and "www.", as the editor shows it.
export function linkText(link) {
  return link.replace(/^[a-z]+:\/\//i, "").replace(/^www\./i, "");
}


// The same link copied with or without "www.", a fragment or a trailing slash.
export function linkKey(link) {
  return linkText(link).replace(/#.*$/, "").replace(/\/+$/, "").toLowerCase();
}

// The queued job a link or path belongs to, or null.
export function queuedJobFor(input, jobs) {
  const key = linkKey(input);
  const matches = (known) => Boolean(known) && linkKey(known) === key;
  return jobs.find((job) => matches(job.input) || matches(job.source?.url)) ?? null;
}

function kindFromInput(input) {
  if (AUDIO_FILE.test(input) && !/^https?:/i.test(input)) return "file";
  try {
    const host = new URL(input).hostname;
    return LINK_HOSTS.find((entry) => entry.pattern.test(host))?.kind ?? "soundcloud";
  } catch {
    // Not a URL: something typed or dropped that isn't resolved yet.
    return "soundcloud";
  }
}

export function sourceKind(job) {
  return job.source?.kind ?? kindFromInput(job.input);
}

export function sourceName(job) {
  return text.sources[sourceKind(job)];
}

export function jobTitle(job) {
  if (job.kind === "set") return job.source?.playlist?.title ?? linkText(job.input);
  if (job.tags?.title) return job.tags.title;
  if (job.kind === "retag") return baseName(job.input);
  return job.source?.title ?? linkText(job.input);
}

export function jobArtist(job) {
  if (job.kind === "set") return job.source?.playlist?.owner ?? job.source?.uploader ?? "";
  return job.tags?.artist || job.source?.uploader || "";
}

// The second line of a queue item: who it's by and where it's from.
export function jobLine(job) {
  if (job.kind === "retag") return text.retagLine;
  if (job.kind === "set") return text.setLine(jobArtist(job), job.children.length);
  return text.itemLine(jobArtist(job), sourceName(job));
}

// The download's progress as a whole percentage, or null when nothing is downloading.
export function downloadPct(job) {
  const pct = job.progress?.pct;
  return pct == null ? null : Math.round(pct);
}

// Save was pressed before the audio arrived, so the track saves once the download is done. A
// set's own job and a re-tagged file have no download to wait for.
export function waitsForDownload(job) {
  return job.state === "saving" && job.kind === "track" && !job.staged_audio;
}

// The side of the square that gets embedded, in the pixels Python crops from, or null.
export function coverSide(job) {
  const size = job.cover_size;
  if (!size) return null;
  const crop = job.artwork?.crop;
  if (!crop) return Math.min(size.width, size.height);
  return Math.round(Math.min(crop.width * size.width, crop.height * size.height));
}

// Places a cover image so the crop's square fills its frame. A centered square needs nothing.
export function cropStyle(crop) {
  if (!crop) return undefined;
  return {
    width: `${100 / crop.width}%`,
    height: `${100 / crop.height}%`,
    left: `${(-crop.left / crop.width) * 100}%`,
    top: `${(-crop.top / crop.height) * 100}%`,
  };
}

// What an error says more exactly than its kind: its reason, such as a file in use, or else that
// a disk error was about sc-mp3's own data folder rather than the library (TECH_SPEC §9).
export function errorVariant(job) {
  const error = job.error;
  if (!error) return null;
  if (error.reason) return error.reason;
  return error.where === "data" ? "data" : null;
}

// An entry with the parts its variant replaces, for a table of kinds such as ERROR_KINDS.
function withVariant(entry, job) {
  return { ...entry, ...(entry?.variants?.[errorVariant(job)] ?? {}) };
}

export function errorKind(job) {
  const kind = job.error?.kind;
  return { kind, ...withVariant(ERROR_KINDS[kind] ?? UNKNOWN_KIND, job) };
}

// The words for a job's error kind, with its variant's in place of the kind's own.
function errorWords(job) {
  const entry = text.errors[job.error?.kind];
  return entry ? withVariant(entry, job) : null;
}

// The headline and detail of a job's error, worded for its source and settings.
export function errorText(job, settings) {
  const entry = errorWords(job);
  if (!entry) return { headline: strings.shell.unknownError(job.error?.kind), detail: null };
  const context = {
    source: sourceName(job),
    folder: folderLabel(settings?.library_dir ?? ""),
    savedOn: job.duplicate ? savedOn(job.duplicate.saved_at) : null,
  };
  return { headline: entry.headline(context), detail: entry.detail?.(context) ?? null };
}

function reviewStatus(job) {
  if (job.staged_audio) return text.status.audioReady;
  const pct = downloadPct(job);
  return pct == null ? text.status.needsReview : text.status.downloadingForReview(pct);
}

function savingStatus(job) {
  return waitsForDownload(job) ? text.status.savesWhenDone : text.status.saving;
}

function savedStatus(job) {
  return job.kind === "retag" ? text.status.retagSaved : text.status.saved;
}

// The status line for each state, and the tone it's shown in.
const STATUS = {
  queued: () => ({ text: text.status.queued, tone: "muted" }),
  resolving: () => ({ text: text.status.resolving, tone: "muted" }),
  needs_review: (job) => ({ text: reviewStatus(job), tone: "warn" }),
  saving: (job) => ({ text: savingStatus(job), tone: "muted" }),
  saved: (job) => ({ text: savedStatus(job), tone: "ok" }),
  skipped: () => ({ text: text.status.skipped, tone: "muted" }),
  error: (job) => ({
    text: errorWords(job)?.status ?? text.unknownErrorStatus,
    tone: errorKind(job).tone,
  }),
};

export function jobStatus(job) {
  return (STATUS[job.state] ?? STATUS.queued)(job);
}

// "Oct 3", or "Oct 3, 2025" when it isn't this year.
export function savedOn(isoDate) {
  if (!isoDate) return null;
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return null;
  const sameYear = date.getFullYear() === new Date().getFullYear();
  const options = sameYear ? { month: "short", day: "numeric" } : { dateStyle: "medium" };
  return new Intl.DateTimeFormat("en-US", options).format(date);
}

// The audio line for a job, such as "AAC 160 kbps stream → MP3 320 kbps", or null.
export function audioLine(job, settings) {
  if (job.kind === "retag") return text.retagAudio;
  const format = job.format?.format;
  if (!format) return null;
  const target = settings?.audio_format ?? "mp3_320";
  const from = audioSource(format);
  if (isCopied(format, target)) return text.audioCopied(from);
  return text.audioConverted(from, text.audioTargets[target]);
}

function codecName(format) {
  const codec = format.codec ?? format.ext;
  return text.codecNames[codec] ?? codec.toUpperCase();
}

function audioSource(format) {
  if (format.is_original) return text.audioOriginal(codecName(format));
  return text.audioStream(codecName(format), format.bitrate ? Math.round(format.bitrate) : null);
}

// MP3 sources stay as they are, and so does AAC when it's kept as .m4a.
function isCopied(format, target) {
  const codec = format.codec ?? format.ext;
  if (codec === "mp3") return target.startsWith("mp3");
  return codec === "aac" && target === "m4a";
}
