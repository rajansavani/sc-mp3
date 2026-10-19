// The built-in Spotify help, shown in the dialog layer.
// TODO(T-062): Build the help panel.
import { html } from "htm/preact";

import { closeDialog } from "../store.js";
import { strings } from "../strings/index.js";

const text = strings.help;

export function SpotifyHelp() {
  return html`
    <div class="placeholder">
      <h2 id="dialog-title" class="placeholder-title">${text.spotifyHelpTitle}</h2>
      <p>${text.notBuilt}</p>
    </div>
    <div class="dialog-actions">
      <button class="button" type="button" onClick=${closeDialog}>${strings.shell.close}</button>
    </div>
  `;
}
