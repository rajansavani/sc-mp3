// The editor for one track: the cover, the fields, the cleanups, the output and the actions.
// TODO(T-023): Build it from the design.
import { html } from "htm/preact";

import { strings } from "../strings/index.js";
import { CoverPanel } from "./CoverPanel.js";

const text = strings.main;

export function TrackEditor({ job }) {
  if (!job) {
    return html`<div class="placeholder"><p>${text.editorEmpty}</p></div>`;
  }
  return html`
    <div class="placeholder">
      <h1 class="placeholder-title">${job.source?.title ?? job.input}</h1>
      <p>${text.states[job.state] ?? job.state}</p>
      <${CoverPanel} job=${job} />
    </div>
  `;
}
