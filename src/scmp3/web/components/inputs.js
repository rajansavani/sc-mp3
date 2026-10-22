// Adding links and files to the queue, from the paste field, a paste anywhere, a drop or a picker.
import { useState } from "preact/hooks";

import { api } from "../api.js";
import { getState, reportError, reportRejected, selectJob } from "../store.js";
import { strings } from "../strings/index.js";

const text = strings.main;

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

// The links in a drop, one a line. Dropped files are left to Python's drop handler on the page's
// body, since only it learns their full paths (TECH_SPEC §16).
export function droppedInputs(dataTransfer) {
  if (dataTransfer.files.length > 0) return "";
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
