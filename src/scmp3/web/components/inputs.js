// Adding links and files to the queue, from the paste field, a paste anywhere, a drop or a picker.
import { useState } from "preact/hooks";

import { api, usesMock } from "../api.js";
import { addToast, getState, reportError, selectJob } from "../store.js";
import { strings } from "../strings/index.js";

const text = strings.main;

// A link that's queued already isn't a mistake: its item gets selected, with a short note.
function reportAlreadyQueued(items) {
  if (items[0].job_id) selectJob(items[0].job_id);
  const one = text.rejectReasons.ALREADY_IN_QUEUE;
  addToast("info", items.length === 1 ? one : text.alreadyQueuedMany(items.length));
}

// Shows one toast per reason, so a paste of 50 stray words doesn't bury the window.
function reportRejected(rejected) {
  const byReason = new Map();
  for (const item of rejected) {
    byReason.set(item.reason, [...(byReason.get(item.reason) ?? []), item]);
  }
  const queued = byReason.get("ALREADY_IN_QUEUE");
  byReason.delete("ALREADY_IN_QUEUE");
  if (queued) reportAlreadyQueued(queued);
  for (const [reason, items] of byReason) {
    const why = text.rejectReasons[reason] ?? text.rejectReasons.NOT_SUPPORTED;
    // PASTE_TOO_LONG has no input of its own to name.
    const named = items.filter((item) => item.input);
    if (named.length === 0) addToast("error", why);
    else if (named.length === 1) addToast("error", text.notAddedOne(named[0].input, why));
    else addToast("error", text.notAddedMany(named.length, why));
  }
}

// Selects the first new job unless the one on screen still needs review.
function selectNewJob(added = []) {
  const { jobs, selected_job_id } = getState();
  const current = jobs.find((job) => job.id === selected_job_id);
  if (added.length > 0 && current?.state !== "needs_review") selectJob(added[0]);
}

// Sends text to add_inputs. Returns true when the call went through, even if some items didn't.
export async function addInputs(textOrPaths) {
  const result = await api.add_inputs(textOrPaths);
  if (reportError(result)) return false;
  reportRejected(result.rejected ?? []);
  selectNewJob(result.added);
  return true;
}

// The app's page never learns a dropped file's full path. The mock makes do with its name.
// TODO(T-025): Add a Python drop handler, which gets the full paths, and drop this.
function droppedFilePaths(files) {
  const paths = files.map((file) => file.pywebviewFullPath ?? (usesMock() ? file.name : null));
  if (paths.includes(null)) {
    addToast("error", text.dropFilesNotYet);
    return [];
  }
  return paths;
}

// Dropped items arrive one by one, so they're joined with newlines to keep paths with spaces whole.
export function droppedInputs(dataTransfer) {
  const files = [...dataTransfer.files];
  if (files.length > 0) return droppedFilePaths(files).join("\n");
  const uriList = dataTransfer.getData("text/uri-list");
  // uri-list lines starting with # are comments.
  const links = uriList.split(/\r?\n/).filter((line) => line.trim() && !line.startsWith("#"));
  if (links.length > 0) return links.join("\n");
  return dataTransfer.getData("text/plain").trim();
}

function carriesInputs(dataTransfer) {
  const types = [...(dataTransfer?.types ?? [])];
  return types.includes("Files") || types.includes("text/uri-list") || types.includes("text/plain");
}

// Drop handlers for a zone that takes links and audio files, and whether something is over it.
export function useInputDrop() {
  const [over, setOver] = useState(false);
  const handlers = {
    onDragOver: (event) => {
      if (!carriesInputs(event.dataTransfer)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      setOver(true);
    },
    onDragLeave: (event) => {
      // Moving between the zone's own children fires dragleave too.
      if (!event.currentTarget.contains(event.relatedTarget)) setOver(false);
    },
    onDrop: (event) => {
      setOver(false);
      // The window's guard skips drops a component has already taken.
      event.preventDefault();
      const dropped = droppedInputs(event.dataTransfer);
      if (dropped) addInputs(dropped);
    },
  };
  return { over, handlers };
}

// Text fields keep their own paste and arrow keys.
export function isEditable(target) {
  if (!(target instanceof Element)) return false;
  if (target.isContentEditable) return true;
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true;
  if (!(target instanceof HTMLInputElement)) return false;
  return !["button", "checkbox", "radio", "submit", "reset", "range", "file"].includes(target.type);
}

// Keys like Enter and the arrows are left alone while a dialog is open.
export function dialogIsOpen() {
  return document.querySelector("dialog[open]") !== null;
}

export function pasteKeys() {
  const mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
  return mac ? text.pasteKeys.mac : text.pasteKeys.other;
}
