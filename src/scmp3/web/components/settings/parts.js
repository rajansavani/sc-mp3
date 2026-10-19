// The pieces the Settings view and first-run setup both show: the logo and the save banner.
import { html } from "htm/preact";

import { strings } from "../../strings/index.js";
import { Icon } from "../icons.js";

export function Logo() {
  return html`
    <svg class="settings-logo" width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
      <rect width="28" height="28" rx="8" />
      <path d="M14 7v10M9.5 12.5L14 17l4.5-4.5M8 20.5h12" />
    </svg>
  `;
}

// An error that isn't one field's, such as settings.json that can't be written.
export function SaveBanner({ message, onDismiss }) {
  if (!message) return null;
  return html`
    <div class="banner settings-banner" data-tone="danger" role="alert">
      <span class="banner-message">
        <${Icon} name="error" size=${20} className="tone-icon" />
        <span>${message}</span>
      </span>
      <button type="button" class="control-button is-small" onClick=${onDismiss}>
        ${strings.shell.dismiss}
      </button>
    </div>
  `;
}
