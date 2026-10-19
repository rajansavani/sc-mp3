// The Workflow section.
import { html } from "htm/preact";

import { SelectSetting, Switches, bind } from "./fields.js";

export function WorkflowSection({ settings, form }) {
  return html`
    <${Switches}
      names=${["review_before_saving", "download_while_reviewing", "offer_clipboard_links"]}
      settings=${settings}
      form=${form}
    />
    <${SelectSetting}
      ...${bind(form, "concurrent_downloads")}
      value=${settings.concurrent_downloads}
      inline
    />
  `;
}
