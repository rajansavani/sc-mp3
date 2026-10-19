// The SoundCloud sign-in dialog, shown in the dialog layer.
// TODO(T-041): Build the three ways to sign in.
import { html } from "htm/preact";

import { closeDialog } from "../store.js";
import { strings } from "../strings/index.js";

const text = strings.account;

export function SignInDialog() {
  return html`
    <div class="placeholder">
      <h2 id="dialog-title" class="placeholder-title">${text.signInTitle}</h2>
      <p>${text.notBuilt}</p>
    </div>
    <div class="dialog-actions">
      <button class="button" type="button" onClick=${closeDialog}>${strings.shell.close}</button>
    </div>
  `;
}
