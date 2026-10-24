// The one-time YouTube support dialog: the question, the download's progress, and how it ended.
// It opens by itself when the first link that needs YouTube support arrives, and again from that
// link's "Add YouTube support" button. Python starts the waiting links once it's installed.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { closeDialog, getState, openDialog, reportError, useStore } from "../store.js";
import { strings } from "../strings/index.js";
import { Icon } from "./icons.js";
import { jobTitle } from "./job-view.js";

const text = strings.youtube;

// The dialog's name in the shell's dialog layer.
export const YOUTUBE_SUPPORT_DIALOG = "youtube_support";
// The error a link gets while YouTube support isn't installed.
const NEEDS_SUPPORT = "youtube_support_missing";
const BYTES_PER_MB = 1_000_000;

function waitingJobs(jobs) {
  return jobs.filter((job) => job.state === "error" && job.error?.kind === NEEDS_SUPPORT);
}

// Opens the dialog when links start waiting for YouTube support, unless another dialog is up.
// `jobs` is null until the snapshot arrives, and links already waiting in it don't open it.
export function useYouTubeSupportPrompt(jobs) {
  const waiting = jobs === null ? null : waitingJobs(jobs).length;
  const before = useRef(null);
  useEffect(() => {
    const wasWaiting = before.current;
    before.current = waiting;
    if (wasWaiting === null || waiting === null) return;
    if (wasWaiting === 0 && waiting > 0 && !getState().dialog) openDialog(YOUTUBE_SUPPORT_DIALOG);
  }, [waiting]);
}

// What the dialog shows, from YouTube support's state. "missing" after a cancel asks again.
const PHASES = { missing: "ask", installing: "downloading", installed: "ready", failed: "failed" };

function megabytes(bytes) {
  return Math.max(1, Math.round(bytes / BYTES_PER_MB));
}

// The download's progress in megabytes, for the dialog and Settings.
export function youtubeProgressText(support) {
  const { downloaded, total } = support;
  if (!downloaded) return text.starting;
  if (total && downloaded >= total) return text.checking;
  if (total) return text.progress(megabytes(downloaded), megabytes(total));
  return text.progressNoTotal(megabytes(downloaded));
}

// A link that still waits counts as fetching, since Python starts it right after the install.
function isFetching(job) {
  return job.state === "queued" || job.state === "resolving" || waitingJobs([job]).length > 0;
}

// About the links that waited: those still fetching their info, or else that they've arrived.
function readyText(waited) {
  const fetching = waited.filter(isFetching);
  if (fetching.length === 1) return text.readyOne(jobTitle(fetching[0]));
  if (fetching.length > 1) return text.readyMany(fetching.length);
  if (waited.length === 1) return text.arrivedOne(jobTitle(waited[0]));
  if (waited.length > 1) return text.arrivedMany(waited.length);
  return text.readyNone;
}

function Ask({ onStart }) {
  return html`
    <div class="youtube-support">
      <h2 id="dialog-title" class="youtube-support-title">${text.askTitle}</h2>
      <p>${text.askBody}</p>
      <p class="youtube-support-detail">${text.askDetail}</p>
      <div class="dialog-actions">
        <button type="button" class="control-button is-large is-ghost" onClick=${closeDialog}>
          ${text.notNow}
        </button>
        <button type="button" class="control-button is-large is-primary" autofocus onClick=${onStart}>
          ${text.download}
        </button>
      </div>
    </div>
  `;
}

function Downloading({ support }) {
  const pct = Math.round(support.pct ?? 0);
  const cancel = async () => reportError(await api.cancel_youtube_support());
  return html`
    <div class="youtube-support">
      <h2 id="dialog-title" class="youtube-support-title">${text.downloadingTitle}</h2>
      <span
        class="progress-track youtube-support-progress"
        role="progressbar"
        aria-label=${text.progressLabel}
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow=${pct}
      >
        <span class="progress-fill" style=${{ "--progress": `${pct}%` }}></span>
      </span>
      <span class="youtube-support-detail">${youtubeProgressText(support)}</span>
      <div class="dialog-actions">
        <button type="button" class="control-button is-large" onClick=${cancel}>${text.cancel}</button>
      </div>
    </div>
  `;
}

function Ready({ waited }) {
  return html`
    <div class="youtube-support" role="status">
      <span class="youtube-support-ready">
        <${Icon} name="check" size=${22} className="youtube-support-check" />
        <h2 id="dialog-title" class="youtube-support-title">${text.readyTitle}</h2>
      </span>
      <p class="youtube-support-note">${readyText(waited)}</p>
      <div class="dialog-actions">
        <button type="button" class="control-button is-large is-primary" autofocus onClick=${closeDialog}>
          ${text.done}
        </button>
      </div>
    </div>
  `;
}

function Failed({ support, onStart }) {
  const why = text.failed[support.error] ?? text.failed.other;
  return html`
    <div class="youtube-support" role="alert">
      <h2 id="dialog-title" class="youtube-support-title">${text.failedTitle}</h2>
      <p class="youtube-support-note">${why} ${text.soundcloudStillWorks}</p>
      <div class="dialog-actions">
        <button type="button" class="control-button is-large is-ghost" onClick=${closeDialog}>
          ${text.notNow}
        </button>
        <button type="button" class="control-button is-large is-primary" onClick=${onStart}>
          ${text.tryAgain}
        </button>
      </div>
    </div>
  `;
}

// Shown in the shell's dialog layer. The links waiting when the download starts are kept, so
// the ready message can name them once they've moved on to fetching their info.
export function YouTubeSupportDialog() {
  const { components, jobs } = useStore();
  const support = components?.youtube_support ?? { state: "missing" };
  const [waitedIds, setWaitedIds] = useState(() => waitingJobs(jobs).map((job) => job.id));
  const start = async () => {
    setWaitedIds(waitingJobs(getState().jobs).map((job) => job.id));
    reportError(await api.install_youtube_support());
  };
  useEffect(() => {
    // Installed while these links were on their way, so the call only starts them again.
    if (support.state === "installed" && waitedIds.length > 0) start();
  }, []);
  // As they are now, so a title shows as soon as its info arrives.
  const waited = jobs.filter((job) => waitedIds.includes(job.id));
  const phase = PHASES[support.state] ?? "ask";
  if (phase === "downloading") return html`<${Downloading} support=${support} />`;
  if (phase === "ready") return html`<${Ready} waited=${waited} />`;
  if (phase === "failed") return html`<${Failed} support=${support} onStart=${start} />`;
  return html`<${Ask} onStart=${start} />`;
}
