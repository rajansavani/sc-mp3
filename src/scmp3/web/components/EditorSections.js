// The editor's lower sections: the cleanup chips, the output strip and the save actions.
import { html } from "htm/preact";

import { api } from "../api.js";
import { openDialog, reportError } from "../store.js";
import { strings } from "../strings/index.js";
import { Icon } from "./icons.js";
import {
  cancelJob,
  nextToReview,
  openSpotify,
  removeJob,
  revealPath,
  selectAndFocus,
  skipJob,
} from "./job-actions.js";
import { audioLine } from "./audio-line.js";
import { downloadPct, sourceKind, waitsForDownload } from "./job-view.js";
import { baseName, folderLabel, folderOf } from "./paths.js";

const text = strings.main;

function chipLabel(change) {
  if (change.enabled) return change.label;
  const kept = text.keptLabels[change.kind] ?? text.keptLabels.remove_promo;
  return kept(change.span);
}

function Chip({ job, change }) {
  const toggle = async () => {
    reportError(await api.toggle_change(job.id, change.id, !change.enabled));
  };
  return html`
    <button type="button" class="chip" aria-pressed=${String(change.enabled)} onClick=${toggle}>
      <${Icon} name=${change.enabled ? "check" : "undo"} weight=${change.enabled ? 2.2 : 1.8} />
      <span>${chipLabel(change)}</span>
    </button>
  `;
}

// The title the cleanups started from. A file with no title tag names the file it was read from.
function originalTitle(source, uploader) {
  if (source.kind === "file" && !source.title) return text.titleFromFileName(baseName(source.url));
  return text.originalTitle(source.title, uploader);
}

export function Cleanups({ job }) {
  const { source } = job;
  if (!source) return null;
  const changes = job.suggestion?.changes ?? [];
  // A file has no uploader to name.
  const uploader = source.kind === "file" ? null : source.uploader;
  return html`
    <section class="cleanups" aria-labelledby="cleanups-title">
      <div class="cleanups-head">
        <h2 id="cleanups-title" class="cleanups-title">${text.cleanupsTitle[sourceKind(job)]}</h2>
        ${changes.length > 0 && html`<span class="cleanups-hint">${text.cleanupsHint}</span>`}
      </div>
      <p class="cleanups-original">${originalTitle(source, uploader)}</p>
      ${changes.length > 0
        ? html`
            <div class="chips">
              ${changes.map(
                (change) => html`<${Chip} key=${change.id} job=${job} change=${change} />`,
              )}
            </div>
          `
        : html`<p class="cleanups-none">${text.noCleanups}</p>`}
    </section>
  `;
}

// A re-tagged file stays where it is, under its own name, so its folder is left to its banner.
export function OutputStrip({ job, settings }) {
  const retag = job.kind === "retag";
  const audio = audioLine(job, settings);
  const folder = settings?.library_dir ?? "";
  return html`
    <section class="output" aria-label=${text.outputLabel}>
      <div class="output-name">
        <span class="output-label">${text.savesAs}</span>
        <span class="output-file">${retag ? baseName(job.input) : job.filename}</span>
      </div>
      <div class="output-details">
        ${!retag &&
        html`<span class="output-detail" title=${folder}>
          <${Icon} name="folder" />
          <span>${folderLabel(folder)}</span>
        </span>`}
        ${audio &&
        html`
          <span class="output-detail">
            <${Icon} name="bars" />
            <span>${audio}</span>
          </span>
        `}
        <span>${job.cover_url ? text.tagsEmbedded : text.tagsWithoutCover}</span>
      </div>
    </section>
  `;
}

function ReviewActions({ job, onSave }) {
  return html`
    <div class="actions">
      <button type="button" class="control-button is-large is-ghost" onClick=${() => skipJob(job)}>
        ${text.skip}
      </button>
      <button
        type="button"
        class="control-button is-large is-primary save-button"
        onClick=${onSave}
      >
        <span>${job.kind === "retag" ? text.saveRetag : text.save}</span>
        <kbd class="key">${text.enterKey}</kbd>
      </button>
    </div>
  `;
}

// Save was pressed before the audio arrived: the button explains itself and Cancel undoes it.
function WaitingActions({ job }) {
  const pct = downloadPct(job) ?? 0;
  return html`
    <div class="actions-waiting">
      <div class="actions">
        <span class="actions-progress-text">${text.downloading(pct)}</span>
        <button
          type="button"
          class="control-button is-large is-ghost"
          onClick=${() => cancelJob(job)}
        >
          ${text.cancel}
        </button>
        <button type="button" class="control-button is-large saves-later" aria-disabled="true">
          <${Icon} name="clock" size=${18} />
          <span>${text.savesWhenDownloaded}</span>
        </button>
      </div>
      <span class="progress-track" aria-hidden="true">
        <span class="progress-fill" style=${{ "--progress": `${pct}%` }}></span>
      </span>
    </div>
  `;
}

function SavingActions() {
  return html`
    <div class="actions">
      <button type="button" class="control-button is-large saving-button" disabled>
        ${text.savingNow}
      </button>
    </div>
  `;
}

function SavedBanner({ job }) {
  const next = nextToReview(job);
  const folder = folderLabel(folderOf(job.output_path ?? ""));
  return html`
    <div class="banner" data-tone="ok" role="status">
      <span class="banner-message saved-message">
        <${Icon} name="check" size=${20} weight=${2.2} className="tone-icon" />
        <span>
          <span>${text.savedTo(folder)}</span>
          ${job.cover_missing && html`<span class="banner-hint">${text.savedWithoutCover}</span>`}
        </span>
      </span>
      <div class="banner-actions">
        <button type="button" class="control-button" onClick=${() => revealPath(job.output_path)}>
          ${text.showInFolder}
        </button>
        <button type="button" class="control-button" onClick=${openSpotify}>
          ${text.openSpotify}
        </button>
        ${next &&
        html`
          <button type="button" class="control-button" onClick=${() => selectAndFocus(next.id)}>
            ${text.backToReview}
          </button>
        `}
      </div>
    </div>
  `;
}

function RetagSavedBanner({ job }) {
  const name = baseName(job.output_path ?? job.input);
  return html`
    <div class="banner" data-tone="ok" role="status">
      <span class="banner-message">
        <span>
          <span class="banner-title">${text.retagSavedTitle(name)}</span>
          <span class="banner-hint">${text.retagSavedHint}</span>
          ${job.cover_missing &&
          html`<span class="banner-hint">${text.retagSavedWithoutCover}</span>`}
        </span>
      </span>
      <div class="banner-actions">
        <button
          type="button"
          class="control-button"
          onClick=${() => openDialog("spotify_help", { tab: "stale" })}
        >
          ${text.spotifyHelp}
        </button>
      </div>
    </div>
  `;
}

function SkippedNote({ job }) {
  return html`
    <div class="banner" data-tone="neutral" role="status">
      <span class="banner-message">${text.skippedNote}</span>
      <div class="banner-actions">
        <button type="button" class="control-button is-small" onClick=${() => removeJob(job)}>
          ${text.removeFromQueue}
        </button>
      </div>
    </div>
  `;
}

function savedBanner(job) {
  if (job.kind === "retag") return html`<${RetagSavedBanner} job=${job} />`;
  return html`<${SavedBanner} job=${job} />`;
}

// What sits under the review: the save buttons, a save in progress, or how it ended.
export function ActionBar({ job, onSave }) {
  if (job.state === "needs_review") return html`<${ReviewActions} job=${job} onSave=${onSave} />`;
  if (waitsForDownload(job)) return html`<${WaitingActions} job=${job} />`;
  if (job.state === "saving") return html`<${SavingActions} />`;
  if (job.state === "saved") return savedBanner(job);
  if (job.state === "skipped") return html`<${SkippedNote} job=${job} />`;
  return null;
}
