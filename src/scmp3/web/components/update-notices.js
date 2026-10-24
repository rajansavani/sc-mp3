// Toasts about updates: what Check now and Update now found, an engine update that didn't load
// at start-up, and what the daily check changed without anyone asking.
import { onEvent } from "../api.js";
import { addToast } from "../store.js";
import { strings } from "../strings/index.js";
import { engineVersionLabel } from "./versions.js";

const text = strings.settings.engine;

// What was last told about, so each change gets one toast.
let known = { restartTo: null, appUpdate: null, outcome: null };

export function errorText(kind) {
  return text.downloadErrors[kind] ?? strings.shell.errors[kind] ?? strings.shell.unknownError(kind);
}

function restartKey(components) {
  const next = components?.engine?.restart_to;
  return next?.source === "updated" ? next.version : null;
}

function appUpdateKey(components) {
  const update = components?.app_update;
  return update?.state === "available" ? update.version : null;
}

function remember(components) {
  known = {
    restartTo: restartKey(components),
    appUpdate: appUpdateKey(components),
    outcome: components?.updates?.last?.id ?? null,
  };
}

// What an engine answer says, given what the next start will use.
function engineMessage(engine, components) {
  const version = engineVersionLabel(engine.version);
  if (engine.refused === "reverted") return text.refusedReverted(version);
  if (engine.refused) return text.refusedFailed(version);
  if (engine.installed && restartKey(components) === engine.version) {
    return text.restartToUpdate(version);
  }
  return text.upToDate(version);
}

// The toasts for a Settings task: its problem, or else what it found.
function reportOutcome({ task, app, engine }, components) {
  const problem = [app, engine].find((part) => part?.error);
  if (problem) {
    addToast("error", errorText(problem.error));
    return;
  }
  if (task === "update") {
    addToast("info", engineMessage(engine, components));
    return;
  }
  const found = [];
  if (app?.state === "available") found.push(text.appAvailable(app.version));
  if (engine?.installed || engine?.refused) found.push(engineMessage(engine, components));
  if (found.length === 0) found.push(engine ? text.allUpToDate : text.appUpToDate);
  for (const message of found) addToast("info", message);
}

function noticeChanges(components) {
  const before = known;
  remember(components);
  const updates = components?.updates;
  if (updates?.last && updates.last.id !== before.outcome) {
    reportOutcome(updates.last, components);
    return;
  }
  // A Settings task's outcome says what it changed.
  if (updates?.running) return;
  if (known.restartTo && known.restartTo !== before.restartTo) {
    addToast("info", text.updatedInBackground);
  }
  if (known.appUpdate && known.appUpdate !== before.appUpdate) {
    addToast("info", text.appAvailableInBackground(known.appUpdate));
  }
}

// Starts from the snapshot, saying once if an engine update didn't load at this start.
export function watchUpdateNotices(components) {
  remember(components);
  const failedNow = components?.engine?.failed_now;
  if (failedNow) addToast("error", text.failedAtStart(engineVersionLabel(failedNow)));
  onEvent((event) => {
    if (event.type === "components") noticeChanges(event.components);
  });
}
