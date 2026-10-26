// How a set's tracks read in the set editor: each row's title, number and status, whether it can
// be changed, and what the Save button saves.
import { strings } from "../strings/index.js";
import { canNeverSave, downloadPct, jobStatus, waitsForDownload } from "./job-view.js";

const text = strings.sets;

// States in which a track's title and artist can still be edited, as in the track editor.
const EDITABLE = new Set(["needs_review", "error"]);

// Whether Save on a track's set saves it, as the job manager's save() decides: it's included,
// and it waits for review, is a repeat that saving confirms, or can save once its info is in.
export function savesWithSet(child) {
  if (!child.included || child.saves_once_in) return false;
  if (child.state === "needs_review") return true;
  if (child.state === "error" && child.error?.kind === "duplicate") return true;
  return !child.tags && child.state !== "skipped" && !canNeverSave(child);
}

// The mode the set saves in: the one picked, or what SoundCloud's type for it suggests.
export function albumMode(job) {
  return job.album_mode ?? job.default_mode ?? "singles";
}

// The album's fields, cover and mode can change until a track saves, and while none is saving.
export function albumEditable(job) {
  return job.state === "needs_review" && !job.album_settled;
}

export function trackEditable(child) {
  return EDITABLE.has(child.state) && Boolean(child.tags);
}

// A track can be left out or put back until an album's numbers are fixed, unless it's saving
// or done. Singles have no numbers, so they can change after others are saved.
export function inclusionEditable(job, child) {
  const fixed = job.album_settled && albumMode(job) === "album";
  if (fixed || job.state === "skipped" || canNeverSave(child)) return false;
  return !["saving", "saved", "skipped"].includes(child.state);
}

// The title a row shows: the one it'll be saved with, or the set's listing's until it's in.
export function trackTitle(job, child, index) {
  if (child.tags?.title) return child.tags.title;
  return job.source?.playlist?.entries?.[index]?.title ?? text.untitled(index + 1);
}

// The track's number in the album, or "" for a single or a track that's left out.
export function trackNumber(job, child) {
  if (albumMode(job) !== "album" || !child.included) return "";
  return child.tags?.track_no == null ? "" : String(child.tags.track_no);
}

// How many included tracks don't have their info yet, still fetching it or waiting to retry,
// which an album's saves wait for.
export function withoutInfo(job) {
  return job.children.filter((child) => child.included && !child.tags && !canNeverSave(child)).length;
}

function savingStatus(job, child) {
  if (waitsForDownload(child)) return text.trackStatus.savesWhenDone(downloadPct(child) ?? 0);
  if (albumMode(job) === "album" && withoutInfo(job) > 0) return text.trackStatus.waitsForAlbum;
  return text.trackStatus.saving;
}

function reviewStatus(child) {
  if (child.collision) return { text: text.trackStatus.nameTaken, tone: "warn" };
  const pct = downloadPct(child);
  if (!child.staged_audio && pct != null) {
    return { text: text.trackStatus.downloading(pct), tone: "muted", pct };
  }
  if (child.duplicate) return { text: text.trackStatus.duplicate, tone: "warn" };
  return { text: text.trackStatus.ready, tone: "ok" };
}

// A row's status, its tone, and its download's percentage when a bar should show.
export function trackStatus(job, child) {
  if (!child.included && !canNeverSave(child)) return { text: text.trackStatus.leftOut, tone: "muted" };
  // A track waiting to retry shows why, though it saves once its info is in.
  if (child.saves_once_in && child.state !== "error") {
    return { text: text.trackStatus.savesOnceIn, tone: "muted" };
  }
  if (child.state === "needs_review") return reviewStatus(child);
  if (child.state === "saving") {
    const pct = waitsForDownload(child) ? (downloadPct(child) ?? 0) : null;
    return { text: savingStatus(job, child), tone: "muted", pct };
  }
  if (canNeverSave(child)) {
    const reason = text.finalReasons[child.error.kind];
    return reason ? { ...jobStatus(child), text: reason } : jobStatus(child);
  }
  if (child.state === "error") return jobStatus(child);
  const words = text.trackStatus[child.state];
  return { text: words ?? jobStatus(child).text, tone: child.state === "saved" ? "ok" : "muted" };
}

// What Save saves, and whether that's every track it would save with all of them included.
export function tracksToSave(job) {
  const count = job.children.filter(savesWithSet).length;
  const open = job.children.filter((child) => savesWithSet({ ...child, included: true })).length;
  return { count, all: count === open };
}

// How many tracks meant to be saved weren't, which the saved banner mentions.
export function notSaved(job) {
  return job.children.filter((child) => child.included && child.state !== "saved").length;
}
