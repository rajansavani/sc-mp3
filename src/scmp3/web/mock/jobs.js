// The queue's mock calls. The queue itself, and how it moves, is in queue-sim.js.
// ?mock&states adds a job for each warning and error kind.
import { addCover, measureCover } from "./covers.js";
import { refusedFields } from "./field-checks.js";
import {
  emit,
  fetchInfoLater,
  findJob,
  jobs,
  publish,
  retryJob,
  startSaving,
  stopDownload,
} from "./queue-sim.js";
import { cleanedArtists, needsMainArtist, pastedNames } from "./names.js";
import { asksAboutPlaylists, confirmRename, forTheUi } from "./review.js";
import { horizon, PALETTES } from "./sample-art.js";
import { makeJob, SAMPLE_HISTORY } from "./sample-queue.js";
import { albumMode, albumSettled, canNeverSave } from "./sets.js";

const LINK = /^https?:\/\/(?:[\w-]+\.)*(?:soundcloud\.com|youtube\.com|youtu\.be)\//i;
const SET_LINK = /soundcloud\.com\/[^/]+\/sets\//i;
const AUDIO_FILE = /\.(?:mp3|m4a)$/i;
// Text that starts like a path, or names an audio file, such as "My Song.mp3".
const LOCAL_PATH = /^(?:[a-z]:[\\/]|~?[\\/]|\.{1,2}[\\/])|\.(?:mp3|m4a)$/i;
const ARTWORK_KINDS = ["source", "url", "file", "data_url"];

// Links the mock turns down, with the reason code the bridge sends. strings/main.js has the text.
const REJECTIONS = [
  { pattern: /youtube\.com\/playlist/i, reason: "YOUTUBE_PLAYLIST" },
  { pattern: /^https?:\/\/(?:www\.)?soundcloud\.com\/[^/?#]+\/?$/i, reason: "SOUNDCLOUD_NOT_TRACK" },
];

// The made-up cover of the image the picker "chose".
const PICKED_COVER = horizon(PALETTES.dusk, 1400);

function editJob(jobId, edit) {
  const job = findJob(jobId);
  if (!job) return { error: "unknown_job" };
  edit(job);
  publish(job);
  return {};
}

function inputKind(input) {
  if (!LINK.test(input)) return AUDIO_FILE.test(input) ? "retag" : null;
  return SET_LINK.test(input) ? "set" : "track";
}

// Why an input isn't added, as the bridge says it, or null. A queued link names its job.
function rejectionOf(input) {
  const queued = jobs.find((job) => job.input === input);
  if (queued) return { reason: "ALREADY_IN_QUEUE", job_id: queued.id };
  const rejection = REJECTIONS.find((candidate) => candidate.pattern.test(input));
  if (rejection) return { reason: rejection.reason };
  return inputKind(input) ? null : { reason: "NOT_SUPPORTED" };
}

// Like parse_inputs: a line that looks like a path stays whole, and other lines split into words.
function splitInputs(textOrPaths) {
  const lines = Array.isArray(textOrPaths) ? textOrPaths : textOrPaths.split(/\r?\n/);
  const items = lines.flatMap((line) => {
    return LOCAL_PATH.test(line.trim()) ? [line] : line.split(/\s+/);
  });
  return [...new Set(items.map((item) => item.trim()).filter(Boolean))];
}

// A new image goes in with the centered square. The same image with a new crop only crops.
// Images added by hand get their preview once it's scaled down, and the job goes out again.
function newArtwork(job, artwork, crop) {
  if (artwork.kind === "data_url") {
    addCover(job.id, artwork.value, () => publish(job));
    return { kind: "file", value: "pasted-cover.png", crop };
  }
  const value = artwork.kind === "source" ? null : (artwork.value ?? null);
  const unchanged = artwork.kind === job.artwork.kind && value === job.artwork.value;
  if (artwork.kind === "file" && !unchanged) addCover(job.id, PICKED_COVER, () => publish(job));
  if (artwork.kind === "url") measureCover(value, () => publish(job));
  return { kind: artwork.kind, value, crop };
}

export function snapshotPart() {
  return { jobs: jobs.map(forTheUi) };
}

// A file whose re-tag is saved or skipped comes in again in place of its finished item, as in
// the bridge.
function clearFinishedRetag(input) {
  const done = jobs.find((job) => {
    return job.kind === "retag" && job.input === input && ["saved", "skipped"].includes(job.state);
  });
  if (done) remove(done.id);
}

export function add_inputs(text_or_paths) {
  const added = [];
  const rejected = [];
  for (const input of splitInputs(text_or_paths)) {
    clearFinishedRetag(input);
    const rejection = rejectionOf(input);
    if (rejection) {
      rejected.push({ input, ...rejection });
      continue;
    }
    const job = makeJob({ kind: inputKind(input), input });
    jobs.push(job);
    added.push(job.id);
    publish(job);
    fetchInfoLater(job);
  }
  return { added, rejected };
}

// The mock's clipboard holds nothing worth offering. ?mock&clipboard holds a track link instead.
const CLIPBOARD_LINK = "https://soundcloud.com/juno-vale/paper-lanterns";

export function clipboard_link() {
  if (!new URLSearchParams(window.location.search).has("clipboard")) return { link: null };
  const queued = jobs.some((job) => job.input === CLIPBOARD_LINK);
  return { link: queued ? null : CLIPBOARD_LINK };
}

// A refused patch changes nothing, and an accepted value is kept exactly as sent.
export function update_fields(job_id, patch) {
  const fields = refusedFields(patch);
  if (Object.keys(fields).length > 0) return { error: "invalid_fields", fields };
  return editJob(job_id, (job) => Object.assign(job.overrides, patch));
}

export function reset_field(job_id, field) {
  return editJob(job_id, (job) => {
    delete job.overrides[field];
  });
}

// The list wins over the cleanups until it's reset. It answers with the names as kept.
export function set_artists(job_id, main, featured) {
  if ([...main, ...featured].some((name) => typeof name !== "string")) return { error: "internal" };
  const artists = cleanedArtists(main, featured);
  const answer = editJob(job_id, (job) => {
    job.artists = artists;
  });
  return answer.error ? answer : { artists };
}

export function reset_artists(job_id) {
  return editJob(job_id, (job) => {
    job.artists = null;
  });
}

export function split_artist_names(text) {
  return { names: pastedNames(text) };
}

export function toggle_change(job_id, change_id, enabled) {
  return editJob(job_id, (job) => {
    const change = job.suggestion?.changes.find((candidate) => candidate.id === change_id);
    if (change) change.enabled = enabled;
  });
}

export function set_style(job_id, style_id) {
  return editJob(job_id, (job) => {
    job.style = style_id;
  });
}

export function set_artwork(job_id, artwork, crop = null) {
  if (!ARTWORK_KINDS.includes(artwork?.kind)) return { error: "invalid_artwork" };
  if (artwork.kind === "url" && !/^https:\/\//i.test(artwork.value ?? "")) {
    return { error: "invalid_artwork" };
  }
  return editJob(job_id, (job) => {
    job.artwork = newArtwork(job, artwork, crop);
    job.cover_problem = null;
  });
}

function canSave(job) {
  if (!job.included) return false;
  return job.state === "needs_review" || (job.state === "error" && job.error?.kind === "duplicate");
}

// A set saves its included tracks. Those still fetching their info save once it's in.
function tracksOf(job) {
  return job.kind === "set" ? job.children : [job];
}

export function dismiss_cover_problem(job_id) {
  const job = findJob(job_id);
  if (!job) return { error: "unknown_job" };
  job.cover_problem = null;
  publish(job);
  return {};
}

export function save(job_ids, rename_confirmed = false) {
  let held = false;
  let asked = false;
  for (const job of job_ids.flatMap((jobId) => tracksOf(findJob(jobId) ?? { children: [] }))) {
    if (canSave(job) && needsMainArtist(job)) {
      held = true;
      continue;
    }
    if (canSave(job) && !rename_confirmed && asksAboutPlaylists(job)) {
      asked = true;
      continue;
    }
    if (canSave(job)) confirmRename(job);
    if (job.included && (job.state === "queued" || job.state === "resolving")) {
      job.saves_once_in = true;
      publish(job);
    }
    if (!canSave(job)) continue;
    // The name is still taken, so the bridge would ask again.
    if (job.collision) {
      publish(job);
      continue;
    }
    startSaving(job);
    publish(job);
  }
  if (asked) return { error: "renames_song" };
  return held ? { error: "no_main_artist" } : {};
}

export function skip(job_id) {
  return editJob(job_id, (job) => {
    for (const track of [job, ...job.children]) {
      if (track.state === "saved") continue;
      stopDownload(track);
      track.state = "skipped";
      track.staged_audio = null;
    }
  });
}

// A failed re-tag whose Retry would rename the song asks first, as the bridge does.
export function retry(job_id, rename_confirmed = false) {
  const job = findJob(job_id);
  const retag = job?.kind === "retag" && job.state === "error" && job.suggestion;
  if (retag && !rename_confirmed && asksAboutPlaylists(job)) return { error: "renames_song" };
  if (retag) confirmRename(job);
  return editJob(job_id, (found) => {
    for (const track of found.source?.playlist ? found.children : [found]) {
      if (track.state === "error" && !canNeverSave(track)) retryJob(track);
    }
  });
}

// A save waiting for its audio goes back to review, and the download carries on. Otherwise the
// download stops. A set puts each of its waiting saves back in review.
export function cancel(job_id) {
  return editJob(job_id, (job) => {
    if (job.kind === "set") {
      for (const track of job.children) {
        if (track.state === "saving") track.state = "needs_review";
        track.saves_once_in = false;
      }
      return;
    }
    if (job.state === "saving") job.state = "needs_review";
    else stopDownload(job);
  });
}

export function remove(job_id) {
  const index = jobs.findIndex((job) => job.id === job_id);
  if (index === -1) return { error: "unknown_job" };
  stopDownload(jobs[index]);
  jobs.splice(index, 1);
  emit({ type: "job_removed", job_id });
  return {};
}

// Ids that aren't queued any more are left out, as in the bridge.
export function remove_jobs(job_ids) {
  for (const jobId of job_ids) remove(jobId);
  return {};
}

// Skip leaves the job unsaved, and the other answers save it. With `rest_of_set`, the answer
// goes for every track of the same set that's asked.
export function answer_collision(job_id, choice, rest_of_set = false) {
  const job = findJob(job_id);
  if (!job) return { error: "unknown_job" };
  const owner = jobs.find((top) => top.children.includes(job));
  const asked = rest_of_set && owner ? owner.children.filter((track) => track.collision) : [job];
  for (const track of asked) {
    track.collision = null;
    if (choice === "skip") track.state = "skipped";
    else startSaving(track);
  }
  publish(job);
  return {};
}

// A set's album is fixed once a track saves, and its mode can't change while one saves.
function albumLocked(job) {
  return albumSettled(job) || job.children.some((track) => track.state === "saving");
}

export function set_album_mode(job_id, mode) {
  const job = findJob(job_id);
  if (job?.kind !== "set") return { error: "unknown_job" };
  if (albumLocked(job)) return { error: "not_editable" };
  return editJob(job_id, (set) => {
    set.album_mode = mode;
  });
}

export function set_included(job_id, child_id, included) {
  const job = findJob(job_id);
  const child = job?.children.find((candidate) => candidate.id === child_id);
  if (!child) return { error: "unknown_job" };
  const fixed = albumSettled(job) && albumMode(job) === "album";
  if (fixed || ["saving", "saved"].includes(child.state)) return { error: "not_editable" };
  child.included = included;
  if (!included) child.saves_once_in = false;
  publish(job);
  return {};
}

export function history(query = "", limit = 50) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const matches = SAMPLE_HISTORY.filter((entry) => {
    const text = `${entry.title} ${entry.artist} ${entry.album}`.toLowerCase();
    return words.every((word) => text.includes(word));
  });
  return { entries: matches.slice(0, limit) };
}

export function diagnostics() {
  return { text: "sc-mp3 (mock API)\nNo real diagnostics in --dev-ui." };
}
