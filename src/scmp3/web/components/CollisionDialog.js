// The question when a file with the same name is already in the library, under the "Ask me"
// setting. The job waits in review with `collision: {path}` until it's answered (TECH_SPEC §15).
import { html } from "htm/preact";
import { useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError } from "../store.js";
import { strings } from "../strings/index.js";
import { DialogHead, useModal } from "./CoverDialogs.js";
import { focusSelectedItem, skipJob } from "./job-actions.js";
import { folderLabel } from "./paths.js";

const text = strings.main;

// Skip moves on to the next item to review, the same as the Skip button. A set's track stays
// in its set, so its Skip is an answer like the others, and `restOfSet` makes the answer go for
// the set's other tracks too.
async function answer(job, choice, inSet, restOfSet) {
  if (choice === "skip" && !inSet) {
    await skipJob(job);
    return;
  }
  if (reportError(await api.answer_collision(job.id, choice, restOfSet))) return;
  focusSelectedItem();
}

// Closing it without an answer leaves the job in review. Saving it again asks again. For a set's
// track, a box makes the answer go for the rest of the set.
export function CollisionDialog({ job, onClose, inSet = false }) {
  const modal = useModal(onClose);
  const [restOfSet, setRestOfSet] = useState(false);
  const choose = (choice) => {
    onClose();
    answer(job, choice, inSet, inSet && restOfSet);
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
        ${inSet &&
        html`
          <label class="dialog-check">
            <input
              type="checkbox"
              checked=${restOfSet}
              onChange=${(event) => setRestOfSet(event.currentTarget.checked)}
            />
            <span>${text.collisionRestOfSet}</span>
          </label>
        `}
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
