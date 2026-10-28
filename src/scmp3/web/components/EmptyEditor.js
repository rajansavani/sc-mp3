// The editor with nothing in the queue: how to start, and a zone that takes dropped links. Dropped
// audio files reach Python's drop handler on the page's body, which queues them for re-tagging.
import { html } from "htm/preact";

import { api } from "../api.js";
import { openDialog, reportError } from "../store.js";
import { strings } from "../strings/index.js";
import { Icon } from "./icons.js";
import { addInputs, pasteKeys, useInputDrop } from "./inputs.js";

const text = strings.main;

async function openAudioFiles() {
  const picked = await api.pick_audio_files();
  if (reportError(picked) || !picked?.paths?.length) return;
  // Paths go one per line, so spaces inside them don't split them.
  await addInputs(picked.paths.join("\n"));
}

export function EmptyEditor() {
  const drop = useInputDrop();
  return html`
    <div class="editor-empty">
      <div class="empty-zone" data-drop-over=${drop.over ? "true" : undefined} ...${drop.handlers}>
        <${Icon} name="download" size=${56} weight=${1.4} className="empty-icon" />
        <div class="empty-text">
          <h1 class="empty-title">${text.emptyTitle}</h1>
          <p class="empty-body">${text.emptyBody}</p>
        </div>
        <div class="empty-hints">
          <span class="empty-hint">
            <kbd class="empty-key">${pasteKeys()}</kbd>
            <span>${text.pasteHint}</span>
          </span>
          <span>${text.setsToo}</span>
        </div>
        <div class="empty-actions">
          <button type="button" class="control-button" onClick=${openAudioFiles}>
            ${text.openAudioFiles}
          </button>
          <button
            type="button"
            class="control-button is-ghost"
            onClick=${() => openDialog("spotify_help")}
          >
            ${text.spotifyHelp}
          </button>
        </div>
      </div>
    </div>
  `;
}
