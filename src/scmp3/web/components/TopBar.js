// The top bar: the paste field, the account chip and the settings button.
// TODO(T-023): Build it from the design, with drag and drop and Ctrl/Cmd+V anywhere.
import { html } from "htm/preact";
import { useState } from "preact/hooks";

import { api } from "../api.js";
import { openDialog, openView, reportError } from "../store.js";
import { strings } from "../strings/index.js";

const text = strings.main;

function Logo() {
  return html`
    <svg class="top-bar-logo" width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
      <rect width="28" height="28" rx="8" />
      <path d="M14 7v10M9.5 12.5L14 17l4.5-4.5M8 20.5h12" />
    </svg>
  `;
}

export function TopBar() {
  const [links, setLinks] = useState("");

  async function add(event) {
    event.preventDefault();
    if (!links.trim()) return;
    const result = await api.add_inputs(links);
    if (!reportError(result)) setLinks("");
  }

  return html`
    <header class="top-bar">
      <div class="top-bar-brand">
        <${Logo} />
        <span>${text.appName}</span>
      </div>
      <form class="top-bar-paste" onSubmit=${add}>
        <label class="visually-hidden" for="top-bar-paste">${text.pasteLabel}</label>
        <input
          id="top-bar-paste"
          class="top-bar-input"
          type="text"
          placeholder=${text.pastePlaceholder}
          value=${links}
          onInput=${(event) => setLinks(event.currentTarget.value)}
        />
        <button class="button" type="submit">${text.add}</button>
      </form>
      <div class="top-bar-actions">
        <button class="button" type="button" onClick=${() => openDialog("sign_in")}>
          ${text.signIn}
        </button>
        <button class="button" type="button" onClick=${() => openView("settings")}>
          ${text.settings}
        </button>
      </div>
    </header>
  `;
}
