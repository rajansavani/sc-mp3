// The offer under the top bar to add a link the clipboard holds, checked when the window gets
// focus. Python reads the clipboard, since the page can't without asking (TECH_SPEC §16), and
// answers only with a link the queue would take that isn't queued yet.
import { html } from "htm/preact";
import { useEffect, useState } from "preact/hooks";

import { api } from "../api.js";
import { useStore } from "../store.js";
import { strings } from "../strings/index.js";
import { addInputs } from "./inputs.js";
import { linkText, queuedJobFor } from "./job-view.js";

const text = strings.main;

// Links the user turned down this session, so the offer doesn't come back for them.
const declined = new Set();

// The link on the clipboard worth offering when the window gets focus, if the setting is on.
function useOfferedLink(enabled) {
  const [link, setLink] = useState(null);
  useEffect(() => {
    if (!enabled) return undefined;
    async function check() {
      const answer = await api.clipboard_link();
      // A failed read just means no offer this time.
      const found = answer?.link ?? null;
      setLink(found && !declined.has(found) ? found : null);
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
