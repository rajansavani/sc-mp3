// YouTube support in the mock: whether it's installed, and a made-up 40 MB download with progress.
// It starts installed, so the sample queue's YouTube item resolves. ?mock&no_youtube starts
// without it, so a YouTube link brings up the dialog, and ?mock&youtube_fails makes every other
// download stop partway, as the design's prototype does.

const MOCK_VERSION = "2.9.8";
const DOWNLOAD_BYTES = 40_400_000;
const STEPS = 12;
const STEP_MS = 250;

const query = new URLSearchParams(window.location.search);
let attempts = 0;
let timer = null;
const readyListeners = new Set();

let support = status(query.has("no_youtube") ? "missing" : "installed");

function status(state, fields = {}) {
  const version = state === "installed" ? MOCK_VERSION : null;
  return { state, version, pct: null, error: null, downloaded: null, total: null, ...fields };
}

export function youtubeSupport() {
  return support;
}

export function hasYouTubeSupport() {
  return support.state === "installed";
}

// Calls `listener` each time YouTube support finishes installing, as Python retries the jobs.
export function whenYouTubeSupportArrives(listener) {
  readyListeners.add(listener);
}

function set(next, emit) {
  support = next;
  emit();
}

function step(index, failsThisTime, emit) {
  if (failsThisTime && index === Math.floor(STEPS / 2)) {
    timer = null;
    set(status("failed", { error: "offline" }), emit);
    // The snapshot says missing after a failure, as Python's does.
    support = status("missing");
    return;
  }
  if (index > STEPS) {
    timer = null;
    set(status("installed"), emit);
    for (const listener of readyListeners) listener();
    return;
  }
  const downloaded = Math.round((DOWNLOAD_BYTES * index) / STEPS);
  const pct = Math.round((1000 * downloaded) / DOWNLOAD_BYTES) / 10;
  set(status("installing", { pct, downloaded, total: DOWNLOAD_BYTES }), emit);
  timer = setTimeout(() => step(index + 1, failsThisTime, emit), STEP_MS);
}

export function installYouTubeSupport(emit) {
  if (support.state === "installed") {
    emit();
    for (const listener of readyListeners) listener();
    return;
  }
  if (timer !== null) return;
  attempts += 1;
  const failsThisTime = query.has("youtube_fails") && attempts % 2 === 1;
  set(status("installing"), emit);
  timer = setTimeout(() => step(1, failsThisTime, emit), STEP_MS);
}

export function cancelYouTubeSupport(emit) {
  if (timer === null) return;
  clearTimeout(timer);
  timer = null;
  set(status("missing"), emit);
}
