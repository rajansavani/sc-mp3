// The Spotify help section, with the button that opens the help panel.
import { html } from "htm/preact";

import { openDialog } from "../../store.js";
import { strings } from "../../strings/index.js";

const text = strings.settings;

// Opens the Spotify help panel, which first-run setup's last step points to.
export function SpotifyHelpSection() {
  return html`
    <div class="settings-card">
      <span class="settings-card-text">
        <span class="settings-card-title">${text.spotifyHelp.title}</span>
        <span class="settings-card-detail">${text.spotifyHelp.detail}</span>
      </span>
      <button
        type="button"
        class="settings-button-quiet"
        onClick=${() => openDialog("spotify_help")}
      >
        ${text.spotifyHelp.open}
      </button>
    </div>
  `;
}
