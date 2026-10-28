// The editor for one track or re-tagged file: the cover, the fields, the cleanups and the actions.
// A re-tag adds its banner and asks about playlists before a save, from RetagParts.js.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError, useStore } from "../store.js";
import { strings } from "../strings/index.js";
import { CollisionDialog } from "./CollisionDialog.js";
import { CoverPanel } from "./CoverPanel.js";
import { DuplicateBanner, ErrorBanner, ErrorView } from "./EditorBanners.js";
import { EditorFields, fieldId, renderedValue } from "./EditorFields.js";
import { ActionBar, Cleanups, OutputStrip } from "./EditorSections.js";
import { EmptyEditor } from "./EmptyEditor.js";
import { useFieldDrafts } from "./field-drafts.js";
import { dialogIsOpen, isEditable } from "./inputs.js";
import { focusSelectedItem, moveToNextReview, removeJob } from "./job-actions.js";
import { linkText, sourceName } from "./job-view.js";
import { PlaylistDialog, RetagBanner, renamesSong, usePlaylistQuestion } from "./RetagParts.js";

const text = strings.main;

// Jobs that Save acts on. A duplicate error is saved again on purpose.
function canSave(job) {
  return job.state === "needs_review" || (job.state === "error" && job.error?.kind === "duplicate");
}

// Elements where Enter already means something.
const INTERACTIVE = [
  "button",
  "a[href]",
  "input",
  "select",
  "textarea",
  "summary",
  "[contenteditable]",
  "[tabindex]",
].join(", ");

// Enter saves from the page and from the editor's fields, and keeps its meaning everywhere else.
function savesOnEnter(target) {
  if (!(target instanceof Element) || target === document.body) return true;
  if (target.closest(".editor-fields") && isEditable(target)) return true;
  return !target.closest(INTERACTIVE);
}

function useEnterToSave(onEnter) {
  const current = useRef(onEnter);
  current.current = onEnter;
  useEffect(() => {
    function onKeyDown(event) {
      if (event.key !== "Enter" || event.repeat || event.isComposing) return;
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (dialogIsOpen() || !savesOnEnter(event.target)) return;
      event.preventDefault();
      current.current();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);
}

// Sends what's still being typed, then saves. A refused field gets focus instead.
// `confirm` can ask first and resolve false to stay in review. `beforeSave` runs next, so a
// question closed without an answer can come up again. `afterSave` moves focus on, since the
// fields lock while saving.
async function saveJob(job, drafts, { confirm, beforeSave, afterSave }) {
  if (!canSave(job)) return;
  if (!(await drafts.flush())) {
    document.getElementById(fieldId(drafts.firstRefused()))?.focus();
    return;
  }
  if (!(await confirm())) return;
  beforeSave();
  if (reportError(await api.save([job.id]))) return;
  afterSave(job);
}

// The Save button stays on the job, to show where it went. Enter goes on to the next job that
// needs review, so a batch can be saved one Enter at a time.
function useSave(job, drafts, { confirm, beforeSave }) {
  const steps = { confirm, beforeSave };
  return {
    save: () => saveJob(job, drafts, { ...steps, afterSave: focusSelectedItem }),
    saveAndNext: () => saveJob(job, drafts, { ...steps, afterSave: moveToNextReview }),
  };
}

// A re-tag that gives the song a new title, artist or album asks about playlists first, unless
// that was turned off.
function usePlaylistCheck(job, settings, drafts) {
  const question = usePlaylistQuestion();
  const confirm = async () => {
    if (settings?.retag_playlist_warning === false) return true;
    if (!renamesSong(job, drafts.value)) return true;
    return question.ask();
  };
  return { question, confirm };
}

// The question about a file with the same name, shown until it's answered or closed. Closing it
// keeps it away until the next save asks again, even for the same file.
function useCollisionQuestion(job) {
  const [closedFor, setClosedFor] = useState(null);
  const path = job.collision?.path ?? null;
  const open = path !== null && closedFor !== path && job.state === "needs_review";
  return { open, close: () => setClosedFor(path), reopen: () => setClosedFor(null) };
}

function EditorHeader({ job, jobs }) {
  const waiting = jobs.filter((other) => other.state === "needs_review").length;
  // A re-tagged file's path is in its banner instead.
  const link = job.kind === "retag" ? null : (job.source?.url ?? job.input);
  return html`
    <div class="editor-header">
      <div class="editor-heading">
        <h1 class="editor-title" tabindex="-1">${text.reviewTitle}</h1>
        ${waiting > 0 && html`<span class="editor-waiting">${text.waiting(waiting)}</span>`}
      </div>
      ${link && html`<span class="editor-link">${linkText(link)}</span>`}
    </div>
  `;
}

// Info is still on its way, so there's nothing to review yet.
function FetchingView({ job }) {
  const title = job.state === "queued" ? text.queuedTitle : text.fetchingTitle;
  return html`
    <div class="track-editor">
      <div class="editor-identity">
        <span class="editor-source">${sourceName(job)}</span>
        <h1 class="editor-title" tabindex="-1">${title}</h1>
        <span class="editor-link">${linkText(job.input)}</span>
      </div>
      <p class="editor-note">${text.fetchingBody}</p>
      <div>
        <button type="button" class="control-button is-ghost" onClick=${() => removeJob(job)}>
          ${text.removeFromQueue}
        </button>
      </div>
    </div>
  `;
}

function TopBanner({ job, settings, onSave }) {
  if (job.state === "error") {
    return html`<${ErrorBanner} job=${job} settings=${settings} onSave=${onSave} />`;
  }
  if (job.duplicate && job.state === "needs_review") {
    return html`<${DuplicateBanner} job=${job} onSave=${onSave} />`;
  }
  if (job.kind === "retag") return html`<${RetagBanner} job=${job} />`;
  return null;
}

function ReviewForm({ job, jobs, settings }) {
  const drafts = useFieldDrafts(job, renderedValue);
  const question = useCollisionQuestion(job);
  const playlist = usePlaylistCheck(job, settings, drafts);
  const { save, saveAndNext } = useSave(job, drafts, {
    confirm: playlist.confirm,
    beforeSave: question.reopen,
  });
  useEnterToSave(saveAndNext);
  // Once saving starts, the tags are settled. A failed save can still be fixed and retried,
  // except a re-tag's, whose Retry writes the tags the playlist question was about.
  const editable = job.state === "needs_review" || (job.state === "error" && job.kind !== "retag");
  return html`
    <div class="track-editor">
      <${EditorHeader} job=${job} jobs=${jobs} />
      <${TopBanner} job=${job} settings=${settings} onSave=${save} />
      <fieldset class="editor-lock" disabled=${!editable}>
        <div class="editor-columns">
          <${CoverPanel} job=${job} settings=${settings} editable=${editable} />
          <${EditorFields} job=${job} drafts=${drafts} />
        </div>
        <${Cleanups} job=${job} />
      </fieldset>
      <${OutputStrip} job=${job} settings=${settings} />
      <${ActionBar} job=${job} onSave=${save} />
      ${question.open && html`<${CollisionDialog} job=${job} onClose=${question.close} />`}
      ${playlist.question.open &&
      html`<${PlaylistDialog} onClose=${playlist.question.close} />`}
    </div>
  `;
}

function ErrorWithoutReview({ job, settings }) {
  const save = async () => reportError(await api.save([job.id]));
  return html`<${ErrorView} job=${job} settings=${settings} onSave=${save} />`;
}

// Each view is keyed by job, so switching jobs starts it fresh.
export function TrackEditor({ job }) {
  const { jobs, settings } = useStore();
  // With items queued, the queue picks one in a moment, so the empty state doesn't flash.
  if (!job) return jobs.length === 0 ? html`<${EmptyEditor} />` : null;
  const props = { key: job.id, job, settings };
  // Without tags there's nothing to review: info is still coming, or fetching it failed.
  if (!job.tags && job.state === "error") return html`<${ErrorWithoutReview} ...${props} />`;
  if (!job.tags) return html`<${FetchingView} ...${props} />`;
  return html`<${ReviewForm} ...${props} jobs=${jobs} />`;
}
