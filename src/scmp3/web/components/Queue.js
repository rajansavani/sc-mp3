// The queue: every item with its cover, its title and artist as they'll be saved, and its status.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError, selectJob } from "../store.js";
import { strings } from "../strings/index.js";
import { Icon } from "./icons.js";
import { dialogIsOpen, isEditable, useInputDrop } from "./inputs.js";
import { focusEditor } from "./job-actions.js";
import {
  cropStyle,
  downloadPct,
  errorKind,
  jobLine,
  jobStatus,
  jobTitle,
  waitsForDownload,
} from "./job-view.js";
import { useAnnouncements } from "./queue-announcer.js";

const text = strings.main;

const FINISHED = new Set(["saved", "skipped"]);

// Where the queue lands when nothing valid is selected: the first item that needs review.
function defaultSelection(jobs) {
  return (jobs.find((job) => job.state === "needs_review") ?? jobs[0])?.id ?? null;
}

// A download shows a bar while the item waits for review, or while its save waits for the audio.
function showsProgress(job) {
  if (waitsForDownload(job)) return true;
  return job.state === "needs_review" && !job.staged_audio && downloadPct(job) != null;
}

function Thumbnail({ job }) {
  // The URL that failed to load, so a new cover gets its own chance.
  const [failedUrl, setFailedUrl] = useState(null);
  if (job.state === "error") {
    return html`
      <span class="queue-thumb is-error" data-tone=${errorKind(job).tone}>
        <${Icon} name="error" size=${20} className="tone-icon" />
      </span>
    `;
  }
  if (!job.cover_url || failedUrl === job.cover_url) {
    return html`<span class="queue-thumb is-empty"><${Icon} name="music" size=${18} /></span>`;
  }
  const crop = job.artwork?.crop ?? null;
  return html`
    <span class="queue-thumb">
      <img
        class=${crop ? "queue-thumb-image is-cropped" : "queue-thumb-image"}
        src=${job.cover_url}
        alt=""
        style=${cropStyle(crop)}
        onError=${() => setFailedUrl(job.cover_url)}
      />
    </span>
  `;
}

// Enter opens the item in the editor, with focus on its heading. It doesn't save.
function onItemKeyDown(event, job) {
  if (event.key !== "Enter" || event.altKey || event.ctrlKey || event.metaKey) return;
  event.preventDefault();
  selectJob(job.id);
  focusEditor();
}

function QueueItem({ job, selected }) {
  const status = jobStatus(job);
  const pct = downloadPct(job) ?? 0;
  return html`
    <li>
      <button
        type="button"
        class="queue-item"
        data-job-id=${job.id}
        aria-current=${selected ? "true" : undefined}
        onClick=${() => selectJob(job.id)}
        onKeyDown=${(event) => onItemKeyDown(event, job)}
      >
        <${Thumbnail} job=${job} />
        <span class="queue-item-text">
          <span class="queue-item-title">${jobTitle(job)}</span>
          <span class="queue-item-line">${jobLine(job)}</span>
          <span class="queue-item-status" data-tone=${status.tone}>${status.text}</span>
          ${showsProgress(job) &&
          html`
            <span class="progress-track queue-item-progress" aria-hidden="true">
              <span class="progress-fill" style=${{ "--progress": `${pct}%` }}></span>
            </span>
          `}
        </span>
      </button>
    </li>
  `;
}

// Focus follows the selection, so it never stays on a control of the job that was left.
function moveSelection(jobs, selectedId, step) {
  const index = jobs.findIndex((job) => job.id === selectedId);
  const next = jobs[Math.min(jobs.length - 1, Math.max(0, index + step))];
  if (!next || next.id === selectedId) return;
  selectJob(next.id);
  requestAnimationFrame(() => {
    const button = document.querySelector(`.queue-item[data-job-id="${CSS.escape(next.id)}"]`);
    button?.scrollIntoView({ block: "nearest" });
    button?.focus();
  });
}

// ↑ and ↓ move through the queue from anywhere except a text field or a dialog.
function useArrowKeys(jobs, selectedId) {
  const current = useRef({ jobs, selectedId });
  current.current = { jobs, selectedId };
  useEffect(() => {
    function onKeyDown(event) {
      const step = { ArrowUp: -1, ArrowDown: 1 }[event.key];
      if (!step || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (isEditable(event.target) || dialogIsOpen()) return;
      event.preventDefault();
      moveSelection(current.current.jobs, current.current.selectedId, step);
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}

function clearFinished(jobs) {
  for (const job of jobs.filter((candidate) => FINISHED.has(candidate.state))) {
    api.remove(job.id).then(reportError);
  }
}

export function Queue({ jobs, selectedId }) {
  const drop = useInputDrop();
  const announcement = useAnnouncements(jobs);
  useArrowKeys(jobs, selectedId);

  const selectionIsValid = jobs.some((job) => job.id === selectedId);
  useEffect(() => {
    if (!selectionIsValid && jobs.length > 0) selectJob(defaultSelection(jobs));
  }, [selectionIsValid, jobs]);

  const anyFinished = jobs.some((job) => FINISHED.has(job.state));
  const items = jobs.map(
    (job) => html`<${QueueItem} key=${job.id} job=${job} selected=${job.id === selectedId} />`,
  );
  return html`
    <div class="queue" data-drop-over=${drop.over ? "true" : undefined} ...${drop.handlers}>
      <div class="queue-head">
        <h2 class="queue-title">${text.queueTitle(jobs.length)}</h2>
        ${anyFinished &&
        html`
          <button type="button" class="queue-clear" onClick=${() => clearFinished(jobs)}>
            ${text.clearFinished}
          </button>
        `}
      </div>
      ${jobs.length === 0
        ? html`<p class="queue-empty">${text.queueEmpty}</p>`
        : html`<ul class="queue-list">${items}</ul>`}
      <p class="visually-hidden" aria-live="polite">${announcement}</p>
    </div>
  `;
}
