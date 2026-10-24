// The update and component calls, with made-up versions, shaped like app/components_view.py.
// YouTube support is in youtube.js.
//
// ?mock&engine=updated starts on a downloaded engine, ?mock&engine=failed with an update that
// didn't load at start-up, and ?mock&app_update with a newer sc-mp3 out.
import { cancelYouTubeSupport, installYouTubeSupport, youtubeSupport } from "./youtube.js";

const BUILT_IN = "2026.8.19";
const NEWER_ENGINE = "2026.10.10.233141";
const NEWER_APP = "0.2.0";
const RELEASE_PAGE = `https://github.com/rajansavani/sc-mp3/releases/tag/v${NEWER_APP}`;
// How long Check now and Update now take.
const TASK_MS = 700;

const query = new URLSearchParams(window.location.search);

function startingEngine() {
  const engine = {
    version: BUILT_IN,
    source: "built_in",
    built_in_version: BUILT_IN,
    restart_to: null,
    failed_version: null,
    channel: null,
    failed_now: null,
  };
  if (query.get("engine") === "updated") {
    return { ...engine, version: NEWER_ENGINE, source: "updated", channel: "nightly" };
  }
  if (query.get("engine") === "failed") {
    return { ...engine, failed_version: NEWER_ENGINE, failed_now: NEWER_ENGINE };
  }
  return engine;
}

function startingAppUpdate() {
  if (query.has("app_update")) return { state: "available", version: NEWER_APP, url: RELEASE_PAGE };
  return { state: "unknown", version: null, url: null };
}

const engine = startingEngine();
let components = {
  app_version: "0.0.1",
  engine_version: engine.version,
  engine,
  app_update: startingAppUpdate(),
  updates: { running: null, pct: null, last: null },
  youtube_support: youtubeSupport(),
};
// Whether the newer engine was downloaded, so the next start would use it unless reverted.
let downloaded = engine.source === "updated";

function emit(event) {
  window.scmp3.emit(JSON.parse(JSON.stringify(event)));
}

function change(patch) {
  components = { ...components, ...patch };
  emit({ type: "components", components });
}

// What the next start would run, as restart_to says it: null when that's what runs now.
function restartTo(nextIsUpdate) {
  const next = nextIsUpdate
    ? { version: NEWER_ENGINE, source: "updated" }
    : { version: BUILT_IN, source: "built_in" };
  return next.source === components.engine.source ? null : next;
}

function installNewerEngine() {
  const installed = !downloaded;
  downloaded = true;
  change({ engine: { ...components.engine, restart_to: restartTo(true), failed_version: null } });
  return { version: NEWER_ENGINE, installed, refused: null };
}

let outcomes = 0;

// Runs a task the way app/update_checks.py does: running at once, the outcome a moment later.
function runTask(task, findings) {
  if (components.updates.running) return {};
  change({ updates: { ...components.updates, running: task } });
  setTimeout(() => {
    outcomes += 1;
    const last = { id: outcomes, task, app: null, engine: null, ...findings() };
    change({ updates: { running: null, pct: null, last } });
  }, TASK_MS);
  return {};
}

export function snapshotPart() {
  return { components };
}

export function check_updates() {
  return runTask("check", () => {
    change({ app_update: { state: "available", version: NEWER_APP, url: RELEASE_PAGE } });
    return { app: components.app_update, engine: installNewerEngine() };
  });
}

export function update_engine() {
  return runTask("update", () => ({ engine: installNewerEngine() }));
}

export function revert_engine() {
  downloaded = false;
  change({ engine: { ...components.engine, restart_to: restartTo(false), failed_version: null } });
  return {};
}

export function open_logs() {
  return {};
}

// Sends YouTube support's state as youtube.js has it now.
function emitYouTubeSupport() {
  change({ youtube_support: youtubeSupport() });
}

export function install_youtube_support() {
  installYouTubeSupport(emitYouTubeSupport);
  return {};
}

export function cancel_youtube_support() {
  cancelYouTubeSupport(emitYouTubeSupport);
  return {};
}
