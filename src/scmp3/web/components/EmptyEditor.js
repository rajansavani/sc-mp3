// The editor with nothing in the queue: how to start, and a zone that takes dropped links.
// It names SoundCloud tracks and YouTube links; T-051 and T-062 add sets and re-tagging back here.
import { html } from "htm/preact";

import { openDialog } from "../store.js";
import { strings } from "../strings/index.js";
import { Icon } from "./icons.js";
import { pasteKeys, useInputDrop } from "./inputs.js";

const text = strings.main;

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
        </div>
        <div class="empty-actions">
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
