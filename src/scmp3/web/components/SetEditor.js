// The editor for a set: the album fields and cover above the track table, the album or singles
// switch, and the actions that save the set's tracks.
import { html } from "htm/preact";
import { useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError, useStore } from "../store.js";
import { strings } from "../strings/index.js";
import { CollisionDialog } from "./CollisionDialog.js";
import { CoverPanel } from "./CoverPanel.js";
import { ErrorView } from "./EditorBanners.js";
import { Field, fieldId, renderedValue } from "./EditorFields.js";
import { useFieldDrafts } from "./field-drafts.js";
import { focusSelectedItem, openSpotify, removeJob, revealPath, skipJob } from "./job-actions.js";
import { jobTitle, linkText, setCounts, setProgress } from "./job-view.js";
import { folderLabel, folderOf } from "./paths.js";
import { albumEditable, albumMode, notSaved, tracksToSave, withoutInfo } from "./set-tracks.js";
import { SetTrackTable } from "./SetTrackTable.js";

const text = strings.sets;
const mainText = strings.main;

const ALBUM_FIELDS = ["album", "album_artist", "year", "genre"];
const MODES = ["album", "singles"];

// The set's tracks are still being listed, so there's nothing to review yet.
function ListingView({ job }) {
  return html`
    <div class="track-editor">
      <div class="editor-identity">
        <span class="editor-source">${text.eyebrow(job.children.length)}</span>
        <h1 class="editor-title" tabindex="-1">${text.listingTitle}</h1>
        <span class="editor-link">${linkText(job.input)}</span>
      </div>
      <p class="editor-note">${text.listingBody}</p>
      <div>
        <button type="button" class="control-button is-ghost" onClick=${() => removeJob(job)}>
          ${mainText.removeFromQueue}
        </button>
      </div>
    </div>
  `;
}

function ModeSwitch({ job }) {
  const current = albumMode(job);
  const pick = (mode) => {
    if (mode !== current) api.set_album_mode(job.id, mode).then(reportError);
  };
  return html`
    <fieldset class="style-switch set-mode" disabled=${!albumEditable(job)}>
      <legend>${text.saveAs}</legend>
      <div class="segmented">
        ${MODES.map(
          (mode) => html`
            <button
              key=${mode}
              type="button"
              class="segment"
              aria-pressed=${String(mode === current)}
              onClick=${() => pick(mode)}
            >
              ${text.modes[mode]}
            </button>
          `,
        )}
      </div>
    </fieldset>
  `;
}

function SetHeader({ job }) {
  const type = job.source?.playlist?.set_type ?? "playlist";
  return html`
    <div class="set-header">
      <div class="editor-identity">
        <span class="editor-source">${text.eyebrow(job.children.length)}</span>
        <h1 class="editor-title" tabindex="-1">${jobTitle(job)}</h1>
        <span class="editor-link">${linkText(job.source?.url ?? job.input)}</span>
      </div>
      <${ModeSwitch} job=${job} />
    </div>
    <p class="set-mode-note">${text.modeNotes[albumMode(job)]} ${text.setTypes[type] ?? ""}</p>
  `;
}

function AlbumSection({ job, settings, drafts }) {
  const editable = albumEditable(job);
  return html`
    <fieldset class="set-album" disabled=${!editable}>
      <${CoverPanel} job=${job} settings=${settings} editable=${editable} />
      <div class="set-album-fields">
        ${ALBUM_FIELDS.map((name) => html`<${Field} key=${name} name=${name} drafts=${drafts} />`)}
      </div>
    </fieldset>
  `;
}

// Why the album can't change, or what its saves wait for.
function AlbumNote({ job }) {
  if (albumMode(job) !== "album") return null;
  const waiting = withoutInfo(job);
  if (job.state === "saving" && waiting > 0) {
    return html`<p class="note" role="status">${text.albumWaiting(waiting)}</p>`;
  }
  if (job.album_settled && job.state !== "saved") {
    return html`<p class="note">${text.albumSettled}</p>`;
  }
  return null;
}

function SaveButtons({ job, onSave }) {
  const { count, all } = tracksToSave(job);
  const failed = setCounts(job).failed;
  const label = count === 0 ? text.noneSelected : all ? text.saveAll(count) : text.saveSelected(count);
  const retry = async () => reportError(await api.retry(job.id));
  return html`
    <div class="actions">
      ${failed > 0 &&
      html`
        <button type="button" class="control-button is-large" onClick=${retry}>
          ${text.retryFailed(failed)}
        </button>
      `}
      <button type="button" class="control-button is-large is-ghost" onClick=${() => skipJob(job)}>
        ${text.skipSet}
      </button>
      <button
        type="button"
        class="control-button is-large is-primary"
        disabled=${count === 0}
        onClick=${onSave}
      >
        ${label}
      </button>
    </div>
  `;
}

function SavingActions({ job }) {
  const counts = setCounts(job);
  const pct = setProgress(job) ?? 0;
  return html`
    <div class="actions-waiting">
      <div class="actions">
        <span class="actions-progress-text">
          ${text.savingNow(counts.saved, counts.saved + counts.saving)}
        </span>
        <button
          type="button"
          class="control-button is-large is-ghost"
          onClick=${async () => reportError(await api.cancel(job.id))}
        >
          ${mainText.cancel}
        </button>
      </div>
      <span class="progress-track" aria-hidden="true">
        <span class="progress-fill" style=${{ "--progress": `${pct}%` }}></span>
      </span>
    </div>
  `;
}

function SavedBanner({ job }) {
  const saved = job.children.filter((child) => child.state === "saved");
  const first = saved[0]?.output_path ?? "";
  const missing = notSaved(job);
  return html`
    <div class="banner" data-tone="ok" role="status">
      <span class="banner-message">
        <span>
          <span>${text.savedTitle(saved.length, folderLabel(folderOf(first)))}</span>
          ${missing > 0 && html`<span class="banner-hint">${text.savedWithFailures(missing)}</span>`}
        </span>
      </span>
      <div class="banner-actions">
        <button type="button" class="control-button" onClick=${() => revealPath(first)}>
          ${mainText.showInFolder}
        </button>
        <button type="button" class="control-button" onClick=${openSpotify}>
          ${mainText.openSpotify}
        </button>
      </div>
    </div>
  `;
}

function SkippedNote({ job }) {
  return html`
    <div class="banner" data-tone="neutral" role="status">
      <span class="banner-message">${mainText.skippedNote}</span>
      <div class="banner-actions">
        <button type="button" class="control-button is-small" onClick=${() => removeJob(job)}>
          ${mainText.removeFromQueue}
        </button>
      </div>
    </div>
  `;
}

function SetActions({ job, settings, onSave }) {
  if (job.state === "saving") return html`<${SavingActions} job=${job} />`;
  if (job.state === "saved") return html`<${SavedBanner} job=${job} />`;
  if (job.state === "skipped") return html`<${SkippedNote} job=${job} />`;
  return html`
    <div class="set-footer">
      <span class="set-saves-to">${text.savesTo(folderLabel(settings?.library_dir ?? ""))}</span>
      <${SaveButtons} job=${job} onSave=${onSave} />
    </div>
  `;
}

// The question about a taken name, for the first track asked whose question wasn't closed.
// Each track has its own dialog, and Save brings the closed ones back.
function TrackQuestion({ job, closed, onClose }) {
  const asked = (track) => track.collision && track.state === "needs_review";
  const child = job.children.find((track) => asked(track) && !closed.has(track.collision.path));
  if (!child) return null;
  const close = () => onClose(child.collision.path);
  return html`<${CollisionDialog} key=${child.id} job=${child} inSet=${true} onClose=${close} />`;
}

// The paths of the questions closed without an answer, until the next Save.
function useClosedQuestions() {
  const [closed, setClosed] = useState(() => new Set());
  return {
    closed,
    close: (path) => setClosed((before) => new Set([...before, path])),
    reopen: () => setClosed(new Set()),
  };
}

// Sends what's still being typed in the album fields and the table, then saves the set's tracks.
// `beforeSave` runs first, so questions closed without an answer can come up again.
function useSaveSet(job, albumDrafts, beforeSave) {
  const rows = useRef(new Map()).current;
  const register = (childId, drafts) => {
    if (drafts) rows.set(childId, drafts);
    else rows.delete(childId);
  };
  const save = async () => {
    const flushed = await Promise.all([albumDrafts, ...rows.values()].map((d) => d.flush()));
    if (!flushed[0]) {
      document.getElementById(fieldId(albumDrafts.firstRefused()))?.focus();
      return;
    }
    beforeSave();
    if (reportError(await api.save([job.id]))) return;
    focusSelectedItem();
  };
  return { save, register };
}

function SetReview({ job, settings }) {
  const drafts = useFieldDrafts(job, renderedValue);
  const [openId, setOpenId] = useState(null);
  const questions = useClosedQuestions();
  const { save, register } = useSaveSet(job, drafts, questions.reopen);
  return html`
    <div class="track-editor set-editor">
      <${SetHeader} job=${job} />
      ${albumMode(job) === "album" &&
      html`<${AlbumSection} job=${job} settings=${settings} drafts=${drafts} />`}
      <${AlbumNote} job=${job} />
      <${SetTrackTable} job=${job} openId=${openId} setOpenId=${setOpenId} register=${register} />
      <${SetActions} job=${job} settings=${settings} onSave=${save} />
      <${TrackQuestion} job=${job} closed=${questions.closed} onClose=${questions.close} />
    </div>
  `;
}

// Each view is keyed by job, so switching jobs starts it fresh.
export function SetEditor({ job }) {
  const { settings } = useStore();
  const props = { key: job.id, job, settings };
  if (job.state === "error" && !job.source) {
    const save = async () => reportError(await api.save([job.id]));
    return html`<${ErrorView} ...${props} onSave=${save} />`;
  }
  if (!job.source) return html`<${ListingView} ...${props} />`;
  return html`<${SetReview} ...${props} />`;
}
