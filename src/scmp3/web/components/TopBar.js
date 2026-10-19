// The top bar: the paste field, the account slot and the settings button, plus the clipboard offer.
import { html } from "htm/preact";
import { useEffect, useState } from "preact/hooks";

import { openDialog, openView } from "../store.js";
import { strings } from "../strings/index.js";
import { ClipboardOffer } from "./ClipboardOffer.js";
import { Icon } from "./icons.js";
import { addInputs, dialogIsOpen, isEditable, useInputDrop } from "./inputs.js";

const text = strings.main;

function Logo() {
  return html`
    <svg class="top-bar-logo" width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
      <rect width="28" height="28" rx="8" />
      <path d="M14 7v10M9.5 12.5L14 17l4.5-4.5M8 20.5h12" />
    </svg>
  `;
}

// Ctrl/Cmd+V outside a text field adds whatever links the clipboard holds.
function usePasteAnywhere() {
  useEffect(() => {
    function onPaste(event) {
      // The cover takes pasted images first, and marks the paste as handled.
      if (event.defaultPrevented || isEditable(event.target) || dialogIsOpen()) return;
      const pasted = event.clipboardData?.getData("text/plain").trim();
      if (!pasted) return;
      event.preventDefault();
      addInputs(pasted);
    }
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, []);
}

// TODO(T-041): Show the account chip here once signing in works.
function AccountSlot() {
  return html`
    <button
      type="button"
      class="control-button is-ghost top-bar-account"
      onClick=${() => openDialog("sign_in")}
    >
      <${Icon} name="person" size=${18} />
      <span>${text.signIn}</span>
    </button>
  `;
}

function PasteForm({ dropOver }) {
  const [links, setLinks] = useState("");

  async function add(event) {
    event.preventDefault();
    if (!links.trim()) return;
    if (await addInputs(links)) setLinks("");
  }

  return html`
    <form class="top-bar-paste" onSubmit=${add}>
      <label class="visually-hidden" for="top-bar-paste">${text.pasteLabel}</label>
      <input
        id="top-bar-paste"
        class="top-bar-input"
        data-drop-over=${dropOver ? "true" : undefined}
        type="text"
        placeholder=${text.pastePlaceholder}
        value=${links}
        onInput=${(event) => setLinks(event.currentTarget.value)}
      />
      <button class="control-button top-bar-add" type="submit">${text.add}</button>
    </form>
  `;
}

export function TopBar() {
  const drop = useInputDrop();
  usePasteAnywhere();

  return html`
    <header class="top-bar" ...${drop.handlers}>
      <div class="top-bar-brand">
        <${Logo} />
        <span>${text.appName}</span>
      </div>
      <${PasteForm} dropOver=${drop.over} />
      <div class="top-bar-actions">
        <${AccountSlot} />
        <button
          type="button"
          class="control-button is-ghost is-icon"
          aria-label=${text.settings}
          onClick=${() => openView("settings")}
        >
          <${Icon} name="sliders" size=${20} />
        </button>
      </div>
    </header>
    <${ClipboardOffer} />
  `;
}
