// The options menu, as a full-window view.
// TODO(T-024): Build every section, with live previews.
import { html } from "htm/preact";

import { closeView } from "../store.js";
import { strings } from "../strings/index.js";

const text = strings.settings;

export function Settings() {
  return html`
    <div class="app-view">
      <div class="placeholder">
        <h1 class="placeholder-title">${text.title}</h1>
        <p>${text.notBuilt}</p>
        <button class="button" type="button" onClick=${closeView}>${strings.shell.back}</button>
      </div>
    </div>
  `;
}
