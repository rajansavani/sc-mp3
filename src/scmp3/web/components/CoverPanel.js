// The cover preview, with Replace and Crop.
// TODO(T-023): Build it from the design, with the crop dialog and the cover warnings.
import { html } from "htm/preact";

import { strings } from "../strings/index.js";

export function CoverPanel() {
  return html`<div class="cover-panel">${strings.main.coverLabel}</div>`;
}
