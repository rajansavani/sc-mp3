// The editor's parts for re-tagging a file you already have: the banner that says so, and the
// question before a save that changes what Spotify's playlists know the song by (PLAN §4.7).
import { html } from "htm/preact";
import { useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError, setSettings } from "../store.js";
import { strings } from "../strings/index.js";
import { DialogHead, useModal } from "./CoverDialogs.js";
import { Icon } from "./icons.js";
import { folderLabel } from "./paths.js";

const text = strings.main;

export function RetagBanner({ job }) {
  return html`
    <div class="banner retag-banner" data-tone="neutral" role="status">
      <span class="banner-message">
        <${Icon} name="pencil" size=${20} className="tone-icon" />
        <span>
          <span class="banner-title">${text.retagTitle}</span>
          <span class="banner-hint">${text.retagBody}</span>
          <span class="banner-path">${folderLabel(job.input)}</span>
        </span>
      </span>
    </div>
  `;
}

// The playlist question, as a promise: `ask()` resolves true to save, false to stay in review.
export function usePlaylistQuestion() {
  const [open, setOpen] = useState(false);
  const answer = useRef(null);
  function ask() {
    setOpen(true);
    return new Promise((resolve) => {
      answer.current = resolve;
    });
  }
  function close(save) {
    setOpen(false);
    answer.current?.(save);
    answer.current = null;
  }
  return { open, ask, close };
}

// "Don't ask again" turns the warning off in the settings, where it can be turned back on.
async function stopAsking() {
  const updated = await api.update_settings({ retag_playlist_warning: false });
  if (!reportError(updated)) setSettings(updated.settings);
}

export function PlaylistDialog({ onClose }) {
  const modal = useModal(() => onClose(false));
  const [dontAsk, setDontAsk] = useState(false);
  const saveAnyway = async () => {
    if (dontAsk) await stopAsking();
    onClose(true);
  };
  return html`
    <dialog
      ref=${modal.ref}
      class="dialog"
      role="alertdialog"
      aria-labelledby="playlist-title"
      aria-describedby="playlist-text"
      onCancel=${modal.onCancel}
    >
      <div class="dialog-body">
        <${DialogHead}
          id="playlist-title"
          title=${text.playlistTitle}
          onClose=${() => onClose(false)}
        />
        <p id="playlist-text" class="dialog-text">${text.playlistBody}</p>
        <label class="dialog-check">
          <input
            type="checkbox"
            checked=${dontAsk}
            onChange=${(event) => setDontAsk(event.currentTarget.checked)}
          />
          <span>${text.playlistDontAsk}</span>
        </label>
        <div class="dialog-actions">
          <button
            type="button"
            class="control-button is-large is-ghost"
            onClick=${() => onClose(false)}
          >
            ${text.cancel}
          </button>
          <button
            type="button"
            class="control-button is-large is-primary"
            autofocus
            onClick=${saveAnyway}
          >
            ${text.playlistSave}
          </button>
        </div>
      </div>
    </dialog>
  `;
}
