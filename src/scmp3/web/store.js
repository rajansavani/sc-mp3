// App state: the snapshot from Python, kept current by events, plus what the UI tracks itself.
// Keys from Python stay snake_case, and the UI's own keys follow them, so nothing is renamed.
import { useEffect, useState } from "preact/hooks";

import { strings } from "./strings/index.js";

// How long a toast stays up before it closes by itself.
const TOAST_MS = 6000;

let state = {
  ready: false, // true once the snapshot has arrived
  jobs: [],
  settings: null,
  account: null,
  components: null,
  first_run: false,
  view: "main", // "main", or a full-window view such as "settings"
  dialog: null, // the open dialog's name, or null
  selected_job_id: null,
  toasts: [],
};

const listeners = new Set();
// Events that arrive before the snapshot wait here, then apply on top of it.
let earlyEvents = [];
let nextToastId = 1;

function update(patch) {
  state = { ...state, ...patch };
  for (const listener of listeners) listener(state);
}

export function getState() {
  return state;
}

// The current state, re-rendering the component whenever it changes.
export function useStore() {
  const [current, setCurrent] = useState(state);
  useEffect(() => {
    listeners.add(setCurrent);
    // The state may have changed between the first render and this effect.
    setCurrent(state);
    return () => listeners.delete(setCurrent);
  }, []);
  return current;
}

export function loadSnapshot(snapshot) {
  const waiting = earlyEvents;
  earlyEvents = [];
  update({ ...snapshot, ready: true });
  for (const event of waiting) applyEvent(event);
}

function upsertJob(jobs, job) {
  const index = jobs.findIndex((existing) => existing.id === job.id);
  if (index === -1) return [...jobs, job];
  return jobs.map((existing, i) => (i === index ? job : existing));
}

// Progress can be for a queue item or for one track of a set, which has its own id.
function withProgress(jobs, event) {
  const { job_id, pct, speed, eta } = event;
  return jobs.map((job) => {
    if (job.id === job_id) return { ...job, progress: { ...job.progress, pct, speed, eta } };
    if (!job.children.some((child) => child.id === job_id)) return job;
    return { ...job, children: withProgress(job.children, event) };
  });
}

function withoutJob(state, jobId) {
  const selected = state.selected_job_id === jobId ? null : state.selected_job_id;
  return { jobs: state.jobs.filter((job) => job.id !== jobId), selected_job_id: selected };
}

// How each event type changes the state. A new event type needs one entry here.
const EVENT_HANDLERS = {
  job: (current, event) => ({ jobs: upsertJob(current.jobs, event.job) }),
  progress: (current, event) => ({ jobs: withProgress(current.jobs, event) }),
  job_removed: (current, event) => withoutJob(current, event.job_id),
  account: (current, event) => ({ account: event.account }),
  components: (current, event) => ({ components: event.components }),
  toast: (current, event) => ({ toasts: [...current.toasts, makeToast(event.level, event.text)] }),
};

export function applyEvent(event) {
  if (!state.ready) {
    earlyEvents.push(event);
    return;
  }
  const handler = EVENT_HANDLERS[event.type];
  if (!handler) {
    console.warn(`No handler for "${event.type}" events`, event);
    return;
  }
  update(handler(state, event));
}

function makeToast(level, text) {
  const id = nextToastId++;
  setTimeout(() => dismissToast(id), TOAST_MS);
  return { id, level, text };
}

export function addToast(level, text) {
  update({ toasts: [...state.toasts, makeToast(level, text)] });
}

export function dismissToast(id) {
  update({ toasts: state.toasts.filter((toast) => toast.id !== id) });
}

// Shows a toast when a call returned an error, and says whether it did.
export function reportError(result) {
  if (!result || !result.error) return false;
  const text = strings.shell.errors[result.error] ?? strings.shell.unknownError(result.error);
  addToast("error", text);
  return true;
}

export function selectJob(jobId) {
  update({ selected_job_id: jobId });
}

export function openView(view) {
  update({ view });
}

export function closeView() {
  update({ view: "main" });
}

export function openDialog(name) {
  update({ dialog: name });
}

export function closeDialog() {
  update({ dialog: null });
}

// Hides first-run setup for this session only, until the real setup can save that it's done.
// TODO(T-024): Remove this once finishing setup saves first_run_done.
export function skipFirstRun() {
  update({ first_run: false });
}
