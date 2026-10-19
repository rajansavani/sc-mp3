// Toasts, in a live region so screen readers announce them.
// TODO(T-050): Build them from the design.
import { html } from "htm/preact";

import { dismissToast } from "../store.js";
import { strings } from "../strings/index.js";

function Toast({ toast }) {
  return html`
    <div class="toast" data-level=${toast.level}>
      <span>${toast.text}</span>
      <button type="button" class="toast-dismiss" onClick=${() => dismissToast(toast.id)}>
        ${strings.shell.dismiss}
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
      ${toasts.map((toast) => html`<${Toast} key=${toast.id} toast=${toast} />`)}
    </div>
  `;
}
