// The queue: every item with its title, its source and its status.
// TODO(T-023): Build it from the design, with covers, progress bars and arrow-key selection.
import { html } from "htm/preact";

import { selectJob } from "../store.js";
import { strings } from "../strings/index.js";

const text = strings.main;

function jobTitle(job) {
  return job.source?.playlist?.title ?? job.source?.title ?? job.input;
}

function jobStatus(job) {
  const pct = job.progress?.pct;
  if (pct != null && job.state !== "saved") return text.downloading(pct);
  return text.states[job.state] ?? job.state;
}

function QueueItem({ job, selected }) {
  return html`
    <li>
      <button
        type="button"
        class="queue-item"
        aria-current=${selected ? "true" : undefined}
        onClick=${() => selectJob(job.id)}
      >
        <span class="queue-item-title">${jobTitle(job)}</span>
        <span class="queue-item-status">${jobStatus(job)}</span>
      </button>
    </li>
  `;
}

export function Queue({ jobs, selectedId }) {
  const items = jobs.map(
    (job) => html`<${QueueItem} key=${job.id} job=${job} selected=${job.id === selectedId} />`,
  );
  return html`
    <h2 class="queue-title">${text.queueTitle(jobs.length)}</h2>
    ${jobs.length === 0
      ? html`<p class="queue-empty">${text.queueEmpty}</p>`
      : html`<ul class="queue-list">${items}</ul>`}
  `;
}
