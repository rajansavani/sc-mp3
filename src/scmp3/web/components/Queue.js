// The queue: every item with its cover, its title and artist as they'll be saved, and its status.
import { Component } from "preact";
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError, selectJob } from "../store.js";
import { strings } from "../strings/index.js";
import { DialogHead, useModal } from "./CoverDialogs.js";
import { Icon } from "./icons.js";
import { dialogIsOpen, isEditable, useInputDrop } from "./inputs.js";
import { focusEditor, focusSelectedItem } from "./job-actions.js";
import {
  coverCrop,
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
// Errors no retry can fix, which Clear finished takes away too.
const FINAL_ERRORS = new Set(["drm", "preview_only", "unavailable", "geo_blocked"]);

function isFinished(job) {
  return FINISHED.has(job.state) || (job.state === "error" && FINAL_ERRORS.has(job.error?.kind));
}

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
  const crop = coverCrop(job);
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

// An item renders again only when its job or its selection changed. Every event replaces just
// the job it's about, so a queue of hundreds stays quick to update.
class QueueRow extends Component {
  shouldComponentUpdate(next) {
    return next.job !== this.props.job || next.selected !== this.props.selected;
  }

  render({ job, selected }) {
    return html`<${QueueItem} job=${job} selected=${selected} />`;
  }
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

// One call for them all, so clearing 2,000 saved tracks doesn't queue 2,000 answers.
async function clearFinished(jobs) {
  reportError(await api.remove_jobs(jobs.filter(isFinished).map((job) => job.id)));
}

// The question before Save all. It names the items waiting when it opened, and saves only those.
function SaveAllDialog({ jobIds, onClose }) {
  const modal = useModal(onClose);
  const count = jobIds.length;
  const saveThem = async () => {
    onClose();
    if (reportError(await api.save(jobIds))) return;
    focusSelectedItem();
  };
  return html`
    <dialog
      ref=${modal.ref}
      class="dialog"
      role="alertdialog"
      aria-labelledby="save-all-title"
      aria-describedby="save-all-text"
      onCancel=${modal.onCancel}
    >
      <div class="dialog-body">
        <${DialogHead} id="save-all-title" title=${text.saveAllTitle(count)} onClose=${onClose} />
        <p id="save-all-text" class="dialog-text">${text.saveAllBody}</p>
        <div class="dialog-actions">
          <button type="button" class="control-button is-large is-ghost" onClick=${onClose}>
            ${text.cancel}
          </button>
          <button
            type="button"
            class="control-button is-large is-primary"
            autofocus
            onClick=${saveThem}
          >
            ${text.saveAllConfirm(count)}
          </button>
        </div>
      </div>
    </dialog>
  `;
}

// Save all shows once two or more items wait for review, and asks before it saves them.
function SaveAll({ jobs }) {
  const [asking, setAsking] = useState(null);
  const waiting = jobs.filter((job) => job.state === "needs_review").map((job) => job.id);
  return html`
    ${waiting.length > 1 &&
    html`
      <button type="button" class="queue-action is-save-all" onClick=${() => setAsking(waiting)}>
        ${text.saveAll}
      </button>
    `}
    ${asking && html`<${SaveAllDialog} jobIds=${asking} onClose=${() => setAsking(null)} />`}
  `;
}

export function Queue({ jobs, selectedId }) {
  const drop = useInputDrop();
  const announcement = useAnnouncements(jobs);
  useArrowKeys(jobs, selectedId);

  const selectionIsValid = jobs.some((job) => job.id === selectedId);
  useEffect(() => {
    if (!selectionIsValid && jobs.length > 0) selectJob(defaultSelection(jobs));
  }, [selectionIsValid, jobs]);

  const anyFinished = jobs.some(isFinished);
  const items = jobs.map(
    (job) => html`<${QueueRow} key=${job.id} job=${job} selected=${job.id === selectedId} />`,
  );
  return html`
    <div class="queue" data-drop-over=${drop.over ? "true" : undefined} ...${drop.handlers}>
      <div class="queue-head">
        <h2 class="queue-title">${text.queueTitle(jobs.length)}</h2>
        <div class="queue-actions">
          <${SaveAll} jobs=${jobs} />
          ${anyFinished &&
          html`
            <button type="button" class="queue-action" onClick=${() => clearFinished(jobs)}>
              ${text.clearFinished}
            </button>
          `}
        </div>
      </div>
      ${jobs.length === 0
        ? html`<p class="queue-empty">${text.queueEmpty}</p>`
        : html`<ul class="queue-list">${items}</ul>`}
      <p class="visually-hidden" aria-live="polite">${announcement}</p>
    </div>
  `;
}
