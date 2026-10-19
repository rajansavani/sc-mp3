// The queue's mock calls, starting from the sample queue in the design.
import { makeJob, makeSource, makeSuggestion, SAMPLE_HISTORY, sampleQueue } from "./sample-queue.js";

// How long each simulated step takes, such as fetching info or saving.
const STEP_MS = 700;

const LINK = /^https?:\/\/(?:[\w-]+\.)*(?:soundcloud\.com|youtube\.com|youtu\.be)\//i;
const SET_LINK = /soundcloud\.com\/[^/]+\/sets\//i;
const AUDIO_FILE = /\.(?:mp3|m4a)$/i;

// Links the mock turns down, with the reason code the bridge sends. strings/main.js has the text.
const REJECTIONS = [
  { pattern: /youtube\.com\/playlist/i, reason: "YOUTUBE_PLAYLIST" },
  { pattern: /^https?:\/\/(?:www\.)?soundcloud\.com\/[^/?#]+\/?$/i, reason: "SOUNDCLOUD_NOT_TRACK" },
];

// Copied on the way out, like everything that crosses the real bridge.
function emit(event) {
  window.scmp3.emit(JSON.parse(JSON.stringify(event)));
}

const jobs = sampleQueue();

function findJob(jobId) {
  for (const job of jobs) {
    if (job.id === jobId) return job;
    const child = job.children.find((candidate) => candidate.id === jobId);
    if (child) return child;
  }
  return null;
}

// Events carry whole queue items, so a set's track goes out as its whole set.
function publish(job) {
  const owner = jobs.find((top) => top === job || top.children.includes(job));
  if (owner) emit({ type: "job", job: owner });
}

function editJob(jobId, edit) {
  const job = findJob(jobId);
  if (!job) return { error: "unknown_job" };
  edit(job);
  publish(job);
  return {};
}

function later(steps, change) {
  setTimeout(change, steps * STEP_MS);
}

function inputKind(input) {
  if (!LINK.test(input)) return AUDIO_FILE.test(input) ? "retag" : null;
  return SET_LINK.test(input) ? "set" : "track";
}

function rejectionReason(input) {
  const rejection = REJECTIONS.find((candidate) => candidate.pattern.test(input));
  if (rejection) return rejection.reason;
  return inputKind(input) ? null : "NOT_SUPPORTED";
}

function splitInputs(textOrPaths) {
  const items = Array.isArray(textOrPaths) ? textOrPaths : textOrPaths.split(/\s+/);
  return [...new Set(items.map((item) => item.trim()).filter(Boolean))];
}

// The last part of the link or path stands in for the title a source would give.
function titleFromInput(input) {
  const last = input.split(/[/\\?=]/).filter(Boolean).at(-1) ?? input;
  return last.replace(AUDIO_FILE, "").replaceAll("-", " ");
}

function sourceKind(job) {
  if (job.kind === "retag") return "file";
  return /youtu/i.test(job.input) ? "youtube" : "soundcloud";
}

function resolve(job) {
  const title = titleFromInput(job.input);
  job.state = "needs_review";
  job.source = makeSource({
    kind: sourceKind(job),
    id: `mock:${job.id}`,
    url: job.input,
    title,
    uploader: "Sample Artist",
  });
  job.suggestion = makeSuggestion({ base_title: title, main_artists: ["Sample Artist"], album: title });
}

function fetchInfoLater(job) {
  later(1, () => {
    job.state = "resolving";
    publish(job);
  });
  later(3, () => {
    resolve(job);
    publish(job);
  });
}

function fileName(job) {
  const title = job.overrides.title ?? job.suggestion?.base_title ?? job.input;
  const artist = job.overrides.artist ?? job.suggestion?.main_artists.join(", ");
  return artist ? `${artist} - ${title}.mp3` : `${title}.mp3`;
}

function finishSaving(job) {
  job.state = "saved";
  job.progress = null;
  job.output_path = `~/Music/sc-mp3/${fileName(job)}`;
  for (const child of job.children) {
    if (child.included) child.state = "saved";
  }
}

export function snapshotPart() {
  return { jobs };
}

export function add_inputs(text_or_paths) {
  const added = [];
  const rejected = [];
  for (const input of splitInputs(text_or_paths)) {
    const reason = rejectionReason(input);
    if (reason) {
      rejected.push({ input, reason });
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

export function update_fields(job_id, patch) {
  return editJob(job_id, (job) => Object.assign(job.overrides, patch));
}

export function reset_field(job_id, field) {
  return editJob(job_id, (job) => {
    delete job.overrides[field];
  });
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
  return editJob(job_id, (job) => {
    // The real bridge saves a pasted image to a file first.
    const value = artwork.kind === "data_url" ? "pasted-cover.png" : artwork.value;
    const kind = artwork.kind === "data_url" ? "file" : artwork.kind;
    job.artwork = { kind, value, crop };
  });
}

export function save(job_ids) {
  for (const jobId of job_ids) {
    const job = findJob(jobId);
    if (!job || job.state !== "needs_review") continue;
    job.state = "saving";
    publish(job);
    later(1, () => {
      finishSaving(job);
      publish(job);
    });
  }
  return {};
}

export function skip(job_id) {
  return editJob(job_id, (job) => {
    job.state = "skipped";
    job.staged_audio = null;
  });
}

export function retry(job_id) {
  return editJob(job_id, (job) => {
    job.state = "queued";
    job.error = null;
    fetchInfoLater(job);
  });
}

export function cancel(job_id) {
  return editJob(job_id, (job) => {
    job.progress = null;
    job.state = job.state === "saving" ? "needs_review" : "skipped";
  });
}

export function remove(job_id) {
  const index = jobs.findIndex((job) => job.id === job_id);
  if (index === -1) return { error: "unknown_job" };
  jobs.splice(index, 1);
  emit({ type: "job_removed", job_id });
  return {};
}

export function set_album_mode(job_id, mode) {
  return editJob(job_id, (job) => {
    job.album_mode = mode;
  });
}

export function set_included(job_id, child_id, included) {
  const job = findJob(job_id);
  const child = job?.children.find((candidate) => candidate.id === child_id);
  if (!child) return { error: "unknown_job" };
  child.included = included;
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
