// The SoundCloud account section, a placeholder until T-041 builds signing in.
import { html } from "htm/preact";

import { openDialog } from "../../store.js";
import { strings } from "../../strings/index.js";

const text = strings.settings;

// TODO(T-041): Show the signed-in account, with Sign out.
export function AccountSection() {
  return html`
    <div class="settings-card">
      <span class="settings-card-text">
        <span class="settings-card-title">${text.account.notSignedIn}</span>
        <span class="settings-hint">${text.account.notBuilt}</span>
      </span>
      <button type="button" class="settings-button-quiet" onClick=${() => openDialog("sign_in")}>
        ${text.account.signIn}
      </button>
    </div>
  `;
}
