// Toasts in the bottom-left corner, in a live region so screen readers announce them. Each
// closes by itself after a few seconds (store.js), or with its close button.
import { html } from "htm/preact";

import { dismissToast } from "../store.js";
import { strings } from "../strings/index.js";
import { Icon } from "./icons.js";

// Only the newest few stay on screen, so a burst of messages can't cover the window.
const MAX_SHOWN = 4;

// The icon for each level. An info toast has none.
const LEVEL_ICONS = { ok: "check", error: "error" };

function Toast({ toast }) {
  const icon = LEVEL_ICONS[toast.level];
  return html`
    <div class="toast" data-level=${toast.level}>
      ${icon && html`<${Icon} name=${icon} size=${20} className="toast-icon" />`}
      <span class="toast-text">${toast.text}</span>
      <button
        type="button"
        class="toast-dismiss"
        aria-label=${strings.shell.dismiss}
        onClick=${() => dismissToast(toast.id)}
      >
        <${Icon} name="close" size=${16} />
      </button>
    </div>
  `;
}

export function Toasts({ toasts }) {
  return html`
    <div
      class="toasts"
      role="status"
      aria-live="polite"
      aria-label=${strings.shell.notificationsLabel}
    >
      ${toasts.slice(-MAX_SHOWN).map((toast) => html`<${Toast} key=${toast.id} toast=${toast} />`)}
    </div>
  `;
}
