// First-run setup, shown instead of everything else until it's done.
// TODO(T-024): Build the four steps.
import { html } from "htm/preact";

import { skipFirstRun } from "../store.js";
import { strings } from "../strings/index.js";

const text = strings.settings;

export function FirstRun() {
  return html`
    <div class="app-view">
      <div class="placeholder">
        <h1 class="placeholder-title">${text.firstRunTitle}</h1>
        <p>${text.firstRunNotBuilt}</p>
        <button class="button" type="button" onClick=${skipFirstRun}>${text.skipSetup}</button>
      </div>
    </div>
  `;
}
