// The pieces the Settings view and first-run setup both show: the logo and the save banner.
import { html } from "htm/preact";

import { strings } from "../../strings/index.js";

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
    <div class="settings-banner" role="alert">
      <span class="settings-banner-message">
        <svg
          class="settings-banner-icon"
          width="20"
          height="20"
          viewBox="0 0 24 24"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M12 7v6M12 16.5h.01" />
        </svg>
        <span>${message}</span>
      </span>
      <button type="button" class="settings-banner-button" onClick=${onDismiss}>
        ${strings.shell.dismiss}
      </button>
    </div>
  `;
}
