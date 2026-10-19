// What the editor's buttons do to a job, through the API. Failures show up as toasts.
import { api } from "../api.js";
import { addToast, getState, reportError, selectJob, setSettings } from "../store.js";
import { strings } from "../strings/index.js";

export function nextToReview(job) {
  const { jobs } = getState();
  return jobs.find((other) => other.id !== job.id && other.state === "needs_review") ?? null;
}

// Moves focus once the next render is done, since the element it goes to may be new.
function focusAfterRender(find) {
  requestAnimationFrame(() => find()?.focus());
}

// Puts focus on the selected queue item, so it doesn't fall to the page when the editor changes.
export function focusSelectedItem() {
  focusAfterRender(() => document.querySelector('.queue-item[aria-current="true"]'));
}

// Puts focus in the editor: on the title field, so Enter there saves, or on the heading of a
// job that has no fields yet.
export function focusEditor() {
  const field = () => document.querySelector(".app-editor #field-title:not(:disabled)");
  focusAfterRender(() => field() ?? document.querySelector(".app-editor h1[tabindex]"));
}

// The item that takes over the selection when this one leaves: the next, or else the one before.
function neighborOf(job) {
  const { jobs } = getState();
  const index = jobs.findIndex((other) => other.id === job.id);
  return jobs[index + 1] ?? jobs[index - 1] ?? null;
}

// The neighbor is selected first, so there's an item for focus to go to once this one is gone.
export async function removeJob(job) {
  const wasSelected = getState().selected_job_id === job.id;
  const neighbor = neighborOf(job);
  if (wasSelected && neighbor) selectJob(neighbor.id);
  if (reportError(await api.remove(job.id))) {
    if (wasSelected) selectJob(job.id);
    return;
  }
  focusSelectedItem();
}

export const retryJob = async (job) => reportError(await api.retry(job.id));
export const cancelJob = async (job) => reportError(await api.cancel(job.id));
export const revealPath = async (path) => reportError(await api.reveal(path));
export const openSpotify = async () => reportError(await api.open_spotify());
export const updateEngine = async () => reportError(await api.update_engine());

// Skipping moves on to the next item that needs review, since a skipped one needs nothing more.
export async function skipJob(job) {
  if (reportError(await api.skip(job.id))) return;
  const next = nextToReview(job);
  if (next) selectJob(next.id);
  focusSelectedItem();
}

export function selectAndFocus(jobId) {
  selectJob(jobId);
  focusSelectedItem();
}

// Says why the folder wasn't taken, in the words the settings screen uses for it.
function reportRefusedFolder(result) {
  const problem = result.fields?.library_dir;
  const text = problem && strings.settings.fieldErrors[problem.reason];
  if (!text) return reportError(result);
  addToast("error", text({ folder: problem.folder ?? strings.settings.thisFolder }));
  return true;
}

// Picks a new library folder. Retry then saves the job there with the same choices.
export async function chooseAnotherFolder(job) {
  const picked = await api.pick_folder();
  if (reportError(picked) || !picked?.path) return;
  const updated = await api.update_settings({ library_dir: picked.path });
  if (updated?.error === "invalid_settings") {
    reportRefusedFolder(updated);
    return;
  }
  if (reportError(updated)) return;
  setSettings(updated.settings);
  await retryJob(job);
}
