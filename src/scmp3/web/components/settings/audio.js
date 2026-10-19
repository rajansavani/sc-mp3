// The Audio and cover art section.
import { html } from "htm/preact";

import { Selects, Switches } from "./fields.js";

export function AudioSection({ settings, form }) {
  return html`
    <div class="settings-grid">
      <${Selects}
        names=${["audio_format", "cover_max_size", "comment_mode"]}
        settings=${settings}
        form=${form}
      />
    </div>
    <${Switches}
      names=${["keep_lossless_originals", "save_cover_jpg"]}
      settings=${settings}
      form=${form}
    />
  `;
}
