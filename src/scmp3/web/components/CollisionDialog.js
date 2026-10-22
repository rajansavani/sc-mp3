// The question when a file with the same name is already in the library, under the "Ask me"
// setting. The job waits in review with `collision: {path}` until it's answered (TECH_SPEC §15).
import { html } from "htm/preact";

import { api } from "../api.js";
import { reportError } from "../store.js";
import { strings } from "../strings/index.js";
import { DialogHead, useModal } from "./CoverDialogs.js";
import { focusSelectedItem, skipJob } from "./job-actions.js";
import { folderLabel } from "./paths.js";

const text = strings.main;

// Skip moves on to the next item to review, the same as the Skip button.
async function answer(job, choice) {
  if (choice === "skip") {
    await skipJob(job);
    return;
  }
  if (reportError(await api.answer_collision(job.id, choice))) return;
  focusSelectedItem();
}

// Closing it without an answer leaves the job in review. Saving it again asks again.
export function CollisionDialog({ job, onClose }) {
  const modal = useModal(onClose);
  const choose = (choice) => {
    onClose();
    answer(job, choice);
  };
  return html`
    <dialog
      ref=${modal.ref}
      class="dialog"
      role="alertdialog"
      aria-labelledby="collision-title"
      aria-describedby="collision-text"
      onCancel=${modal.onCancel}
    >
      <div class="dialog-body">
        <${DialogHead} id="collision-title" title=${text.collisionTitle} onClose=${onClose} />
        <p id="collision-text" class="dialog-text">${text.collisionBody}</p>
        <p class="banner-path">${folderLabel(job.collision.path)}</p>
        <div class="dialog-actions">
          <button
            type="button"
            class="control-button is-large is-ghost"
            onClick=${() => choose("skip")}
          >
            ${text.collisionSkip}
          </button>
          <button
            type="button"
            class="control-button is-large"
            onClick=${() => choose("replace")}
          >
            ${text.collisionReplace}
          </button>
          <button
            type="button"
            class="control-button is-large is-primary"
            autofocus
            onClick=${() => choose("keep_both")}
          >
            ${text.collisionKeepBoth}
          </button>
        </div>
      </div>
    </dialog>
  `;
}
