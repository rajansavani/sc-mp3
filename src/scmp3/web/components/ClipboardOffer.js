// The offer under the top bar to add a link the clipboard holds, checked when the window gets
// focus. TODO(T-025): Have Python read the clipboard, so the offer works in the app.
import { html } from "htm/preact";
import { useEffect, useState } from "preact/hooks";

import { getState, useStore } from "../store.js";
import { strings } from "../strings/index.js";
import { addInputs } from "./inputs.js";
import { linkText, queuedJobFor } from "./job-view.js";

const text = strings.main;

const LINK = /https?:\/\/[^\s"'<>]+/gi;
const OFFERED_HOSTS = /^(?:[\w-]+\.)*(?:soundcloud\.com|youtube\.com|youtu\.be)$/i;

// Links the user turned down this session, so the offer doesn't come back for them.
const declined = new Set();

function offeredLink(clipboardText) {
  for (const match of clipboardText.matchAll(LINK)) {
    try {
      if (OFFERED_HOSTS.test(new URL(match[0]).hostname)) return match[0];
    } catch {
      // Text that only looks like a link, such as "https://" on its own.
      continue;
    }
  }
  return null;
}

// Reading without a paste makes WebView2 ask the user each time, so the offer only reads once
// reading is allowed.
async function mayReadClipboard() {
  try {
    const permission = await navigator.permissions.query({ name: "clipboard-read" });
    return permission.state === "granted";
  } catch (error) {
    // WebKit doesn't know this permission, and never allows reads without a paste.
    console.debug("The clipboard permission couldn't be checked", error);
    return false;
  }
}

async function readClipboard() {
  if (!(await mayReadClipboard())) return "";
  try {
    return await navigator.clipboard.readText();
  } catch (error) {
    // The clipboard is off limits while the window doesn't have focus.
    console.debug("The clipboard couldn't be read for the link offer", error);
    return "";
  }
}

// The link on the clipboard worth offering when the window gets focus, if the setting is on.
function useOfferedLink(enabled) {
  const [link, setLink] = useState(null);
  useEffect(() => {
    if (!enabled || !navigator.clipboard?.readText) return undefined;
    async function check() {
      const found = offeredLink(await readClipboard());
      const fresh = found && !declined.has(found) && !queuedJobFor(found, getState().jobs);
      setLink(fresh ? found : null);
    }
    window.addEventListener("focus", check);
    if (document.hasFocus()) check();
    return () => window.removeEventListener("focus", check);
  }, [enabled]);
  return [enabled ? link : null, setLink];
}

export function ClipboardOffer() {
  const { settings, jobs } = useStore();
  const [link, setLink] = useOfferedLink(Boolean(settings?.offer_clipboard_links));
  // The offer goes away once its link is queued some other way.
  if (!link || queuedJobFor(link, jobs)) return null;

  const decline = () => {
    declined.add(link);
    setLink(null);
  };
  const accept = () => {
    setLink(null);
    addInputs(link);
  };
  return html`
    <div class="clipboard-offer" role="status">
      <span class="clipboard-offer-text">
        <span class="clipboard-offer-title">${text.clipboardOffer}</span>
        <span class="clipboard-offer-link">${linkText(link)}</span>
      </span>
      <span class="clipboard-offer-actions">
        <button type="button" class="control-button is-small is-ghost" onClick=${decline}>
          ${text.clipboardNotNow}
        </button>
        <button type="button" class="control-button is-small is-primary" onClick=${accept}>
          ${text.clipboardAdd}
        </button>
      </span>
    </div>
  `;
}
