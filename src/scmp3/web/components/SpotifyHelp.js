// The Spotify help panel, from design/A-spotify-help: getting songs into Spotify on a computer and
// a phone, and what to try when they don't show up. It opens in the dialog layer's side panel.
import { html } from "htm/preact";
import { useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { closeDialog, reportError, useStore } from "../store.js";
import { strings } from "../strings/index.js";
import { currentOs } from "./FirstRun.js";
import { Icon } from "./icons.js";
import { openSpotify } from "./job-actions.js";
import { folderLabel } from "./paths.js";

const text = strings.help;

// The tabs in order, each with its panel. A new topic is one more entry.
const TABS = [
  { id: "desktop", Panel: DesktopPanel },
  { id: "phone", Panel: PhonePanel },
  { id: "stale", Panel: StalePanel },
];

// Arrow keys move along the tabs and pick the tab they land on, as a tab list does.
const TAB_KEYS = {
  ArrowRight: (index) => (index + 1) % TABS.length,
  ArrowLeft: (index) => (index - 1 + TABS.length) % TABS.length,
  Home: () => 0,
  End: () => TABS.length - 1,
};

async function openLocalFilesFolder() {
  reportError(await api.open_local_files_folder());
}

function Step({ number, children, action }) {
  return html`
    <li class="help-step">
      <span class="help-step-line">
        <span class="help-badge" aria-hidden="true">${number}</span>
        <span class="help-step-text">${children}</span>
      </span>
      ${action}
    </li>
  `;
}

function DesktopPanel({ settings }) {
  const steps = text.desktopSteps(folderLabel(settings?.library_dir ?? ""));
  return html`
    <p class="help-intro">${text.desktopIntro}</p>
    <ol class="help-steps">
      ${steps.map((step, index) => html`<${Step} key=${index} number=${index + 1}>${step}<//>`)}
    </ol>
    <div>
      <button type="button" class="control-button is-primary" onClick=${openSpotify}>
        ${text.openSpotify}
      </button>
    </div>
  `;
}

function PhonePanel() {
  return html`
    <p class="help-intro">${text.phoneIntro}</p>
    ${text.phoneSections.map(
      (section) => html`
        <section key=${section.title} class="help-section">
          <h2 class="help-section-title">${section.title}</h2>
          <p class="help-section-text">${section.text}</p>
        </section>
      `,
    )}
    <p class="help-note">${text.phoneNote}</p>
  `;
}

// The last step deletes Spotify's list of local files, so it gets a button to find it.
function LocalFilesAction() {
  return html`
    <span class="help-step-action">
      <button type="button" class="control-button is-small is-ghost" onClick=${openLocalFilesFolder}>
        ${text.openLocalFiles}
      </button>
    </span>
    <span class="help-step-hint">${text.localFilesHint[currentOs()]}</span>
  `;
}

function StalePanel() {
  const last = text.staleSteps.length - 1;
  return html`
    <p class="help-intro">${text.staleIntro}</p>
    <ol class="help-steps">
      ${text.staleSteps.map(
        (step, index) => html`
          <${Step}
            key=${index}
            number=${index + 1}
            action=${index === last ? html`<${LocalFilesAction} />` : null}
          >
            ${step}
          <//>
        `,
      )}
    </ol>
  `;
}

function Tabs({ current, onPick }) {
  const buttons = useRef({});
  const onKeyDown = (event) => {
    const move = TAB_KEYS[event.key];
    if (!move) return;
    event.preventDefault();
    const next = TABS[move(TABS.findIndex((tab) => tab.id === current))].id;
    onPick(next);
    buttons.current[next]?.focus();
  };
  return html`
    <div class="help-tabs" role="tablist" aria-label=${text.tabsLabel} onKeyDown=${onKeyDown}>
      ${TABS.map(
        ({ id }) => html`
          <button
            key=${id}
            ref=${(button) => (buttons.current[id] = button)}
            type="button"
            role="tab"
            id=${`help-tab-${id}`}
            class="help-tab"
            aria-selected=${String(id === current)}
            aria-controls="help-panel"
            tabindex=${id === current ? 0 : -1}
            onClick=${() => onPick(id)}
          >
            ${text.tabs[id]}
          </button>
        `,
      )}
    </div>
  `;
}

// `tab` picks the topic it opens on, such as "stale" after re-tagging a file.
export function SpotifyHelp({ tab = "desktop" }) {
  const { settings } = useStore();
  const [current, setCurrent] = useState(TABS.some(({ id }) => id === tab) ? tab : "desktop");
  const { Panel } = TABS.find(({ id }) => id === current);
  return html`
    <div class="help">
      <div class="help-head">
        <h1 id="dialog-title" class="help-title">${text.title}</h1>
        <button
          type="button"
          class="control-button is-ghost is-icon"
          aria-label=${strings.shell.close}
          onClick=${closeDialog}
        >
          <${Icon} name="close" size=${18} />
        </button>
      </div>
      <${Tabs} current=${current} onPick=${setCurrent} />
      <div
        id="help-panel"
        class="help-panel"
        role="tabpanel"
        aria-labelledby=${`help-tab-${current}`}
        tabindex="0"
      >
        <${Panel} settings=${settings} />
      </div>
    </div>
  `;
}
