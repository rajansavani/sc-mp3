// The editor for a set: the album fields above the track table.
// TODO(T-052): Build it from the design.
import { html } from "htm/preact";

import { strings } from "../strings/index.js";

const text = strings.main;

export function SetEditor({ job }) {
  const title = job.source?.playlist?.title ?? job.input;
  return html`
    <div class="placeholder">
      <h1 class="placeholder-title">${title}</h1>
      <p>${text.setTracks(job.children.length)}</p>
    </div>
  `;
}
