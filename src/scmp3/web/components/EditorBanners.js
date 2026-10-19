// The duplicate warning and the error banners, each with the next steps it offers.
import { html } from "htm/preact";

import { openDialog } from "../store.js";
import { strings } from "../strings/index.js";
import { Icon } from "./icons.js";
import {
  chooseAnotherFolder,
  removeJob,
  retryJob,
  revealPath,
  updateEngine,
} from "./job-actions.js";
import { errorKind, errorText, jobLine, jobTitle, savedOn, sourceName } from "./job-view.js";
import { folderLabel } from "./paths.js";

const text = strings.main;

// What each button in an error banner does. `onSave` is the editor's own save.
const ACTIONS = {
  remove: (job) => removeJob(job),
  retry: (job) => retryJob(job),
  retry_now: (job) => retryJob(job),
  update_engine: () => updateEngine(),
  sign_in_go_plus: () => openDialog("sign_in"),
  sign_in_again: () => openDialog("sign_in"),
  choose_folder: (job) => chooseAnotherFolder(job),
  show_it: (job) => revealPath(job.duplicate?.saved_path),
  save_again: (job, onSave) => onSave(),
};

function ActionButtons({ job, onSave, small, primaryLast }) {
  const { actions, primary } = errorKind(job);
  const buttons = actions.map((action, index) => {
    const classes = ["control-button", small && "is-small", primary && index === 0 && "is-primary"];
    return html`
      <button
        key=${action}
        type="button"
        class=${classes.filter(Boolean).join(" ")}
        onClick=${() => ACTIONS[action](job, onSave)}
      >
        ${text.errorActions[action]}
      </button>
    `;
  });
  // In a banner the main action sits last, on the right. In the error view it leads.
  if (primary && primaryLast) buttons.push(buttons.shift());
  return buttons;
}

export function DuplicateBanner({ job, onSave }) {
  const { duplicate } = job;
  const date = savedOn(duplicate.saved_at);
  const title = date ? text.duplicateTitle(date) : text.duplicateTitleUndated;
  return html`
    <div class="banner" data-tone="warn">
      <span class="banner-message">
        <${Icon} name="clock" size=${20} className="tone-icon" />
        <span>
          <span class="banner-title">${title}</span>
          <span class="banner-path">${folderLabel(duplicate.saved_path)}</span>
        </span>
      </span>
      <span class="banner-actions">
        <button type="button" class="control-button is-small" onClick=${() => ACTIONS.show_it(job)}>
          ${text.errorActions.show_it}
        </button>
        <button type="button" class="control-button is-small" onClick=${onSave}>
          ${text.errorActions.save_again}
        </button>
      </span>
    </div>
  `;
}

// An error that came up after the review started, shown above the fields.
// The queue's live region announces the error, so the banner itself has no live role.
export function ErrorBanner({ job, settings, onSave }) {
  const { tone, icon } = errorKind(job);
  const { headline, detail } = errorText(job, settings);
  return html`
    <div class="banner" data-tone=${tone}>
      <span class="banner-message">
        <${Icon} name=${icon} size=${20} className="tone-icon" />
        <span>${detail ? `${headline} ${detail}` : headline}</span>
      </span>
      <span class="banner-actions">
        <${ActionButtons} job=${job} onSave=${onSave} small=${true} primaryLast=${true} />
      </span>
    </div>
  `;
}

// A job that failed before it could be reviewed: what it is, what happened and what to do.
export function ErrorView({ job, settings, onSave }) {
  const { tone, icon, actions } = errorKind(job);
  const { headline, detail } = errorText(job, settings);
  return html`
    <div class="track-editor">
      <div class="editor-identity">
        <span class="editor-source">${sourceName(job)}</span>
        <h1 class="editor-title" tabindex="-1">${jobTitle(job)}</h1>
        <span class="editor-line">${jobLine(job)}</span>
      </div>
      <div class="error-box" data-tone=${tone}>
        <span class="error-box-message">
          <${Icon} name=${icon} size=${22} className="tone-icon" />
          <span class="error-box-text">
            <span class="error-headline">${headline}</span>
            ${detail && html`<span class="error-detail">${detail}</span>`}
          </span>
        </span>
        ${actions.length > 0 &&
        html`
          <span class="error-box-actions">
            <${ActionButtons} job=${job} onSave=${onSave} small=${false} primaryLast=${false} />
          </span>
        `}
      </div>
    </div>
  `;
}
