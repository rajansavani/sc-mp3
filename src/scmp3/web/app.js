// The shell: the main layout with its slots, the full-window views, the dialog layer and toasts.
import { html, render } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api, onEvent } from "./api.js";
import { copyDiagnostics } from "./components/diagnostics.js";
import { FirstRun } from "./components/FirstRun.js";
import { Queue } from "./components/Queue.js";
import { SetEditor } from "./components/SetEditor.js";
import { Settings } from "./components/Settings.js";
import { SignInDialog } from "./components/SignInDialog.js";
import { SpotifyHelp } from "./components/SpotifyHelp.js";
import { Toasts } from "./components/Toasts.js";
import { TopBar } from "./components/TopBar.js";
import { TrackEditor } from "./components/TrackEditor.js";
import { watchUpdateNotices } from "./components/update-notices.js";
import {
  applyEvent,
  closeDialog,
  loadSnapshot,
  openView,
  useStore,
} from "./store.js";
import { strings } from "./strings/index.js";

// Full-window views that replace the main layout, by the name the store keeps in `view`.
const VIEWS = { settings: Settings };

// Dialogs the layer can show, by the name the store keeps in `dialog`, with the frame each sits
// in: the centered dialog, or the side panel on the right.
const DIALOGS = {
  sign_in: { Content: SignInDialog, frame: "dialog" },
  spotify_help: { Content: SpotifyHelp, frame: "side-panel" },
};

// The editor for each kind of job.
const EDITORS = { track: TrackEditor, retag: TrackEditor, set: SetEditor };

// Shortcuts for the whole window. Esc is left to the dialog, which closes itself on it.
const SHORTCUTS = [
  {
    matches: (event) => (event.ctrlKey || event.metaKey) && event.key === ",",
    run: () => openView("settings"),
  },
];

function onKeyDown(event) {
  const shortcut = SHORTCUTS.find((candidate) => candidate.matches(event));
  if (!shortcut) return;
  event.preventDefault();
  shortcut.run();
}

// Unless a component takes a drop, the webview would open a dropped file in place of the app,
// or hand a dropped link to the system browser.
function refuseStrayDrop(event) {
  // A component that takes the drop has already called preventDefault.
  if (event.defaultPrevented) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = "none";
}

// Links never move the window off the app, since pywebview gives any page it shows the whole API.
function keepLinksOutOfTheWindow(event) {
  const link = event.target.closest?.("a[href]");
  if (!link || event.defaultPrevented) return;
  const url = new URL(link.href, window.location.href);
  const here = window.location;
  if (url.origin === here.origin && url.pathname === here.pathname) return;
  event.preventDefault();
  // Web pages open in the system browser, which is where pywebview sends new windows.
  if (url.protocol === "https:" || url.protocol === "http:") window.open(url.href, "_blank");
}

// Listeners on the whole window, added before anything renders.
const WINDOW_LISTENERS = {
  keydown: onKeyDown,
  dragover: refuseStrayDrop,
  drop: refuseStrayDrop,
  click: keepLinksOutOfTheWindow,
};

function Editor({ job }) {
  const EditorForKind = job ? EDITORS[job.kind] : TrackEditor;
  return html`<${EditorForKind} job=${job} />`;
}

function MainLayout({ state }) {
  const job = state.jobs.find((candidate) => candidate.id === state.selected_job_id) ?? null;
  return html`
    <div class="app">
      <${TopBar} />
      <div class="app-body">
        <aside class="app-queue" aria-label=${strings.main.queueLabel}>
          <${Queue} jobs=${state.jobs} selectedId=${state.selected_job_id} />
        </aside>
        <main class="app-editor">
          <${Editor} job=${job} />
        </main>
      </div>
    </div>
  `;
}

function CurrentView({ state }) {
  if (state.first_run) return html`<${FirstRun} />`;
  const View = VIEWS[state.view];
  if (View) return html`<${View} />`;
  return html`<${MainLayout} state=${state} />`;
}

// A native modal <dialog>, which keeps focus inside it and closes on Esc. `props` go to the
// dialog's content, and `children` go in after it.
function DialogLayer({ name, props, children }) {
  const ref = useRef(null);
  const entry = DIALOGS[name] ?? null;
  const Content = entry?.Content ?? null;
  useEffect(() => {
    const dialog = ref.current;
    if (Content && !dialog.open) dialog.showModal();
    if (!Content && dialog.open) dialog.close();
  }, [Content]);

  // The store decides when the dialog closes, so Esc goes through it too.
  const onCancel = (event) => {
    event.preventDefault();
    closeDialog();
  };
  // A dialog that closes itself, such as through <form method="dialog">, tells the store.
  return html`
    <dialog
      ref=${ref}
      class=${entry?.frame ?? "dialog"}
      aria-labelledby="dialog-title"
      onCancel=${onCancel}
      onClose=${closeDialog}
    >
      ${Content && html`<${Content} ...${props} />`} ${Content && children}
    </dialog>
  `;
}

function App() {
  const state = useStore();
  const toasts = html`<${Toasts} toasts=${state.toasts} />`;
  // An open modal dialog makes the rest of the page inert, so toasts move into it meanwhile.
  const dialogOpen = Boolean(DIALOGS[state.dialog]);
  // Nothing renders before the snapshot, so first-run setup doesn't follow a flash of the queue.
  return html`
    ${state.ready && html`<${CurrentView} state=${state} />`}
    <${DialogLayer} name=${state.dialog} props=${state.dialog_props}>
      ${dialogOpen && toasts}
    <//>
    ${!dialogOpen && toasts}
  `;
}

// What the window shows when it couldn't start, so it's never left blank.
function StartFailed() {
  const text = strings.shell;
  const [copied, setCopied] = useState(null);
  const outcome = copied ? text.diagnosticsCopied : text.diagnosticsNotCopied;
  const copy = async () => setCopied(await copyDiagnostics());
  return html`
    <div class="app-view">
      <div class="placeholder" role="alert">
        <h1 class="placeholder-title">${text.startFailedTitle}</h1>
        <p>${text.startFailed}</p>
        <button class="button" type="button" onClick=${copy}>
          ${text.copyDiagnostics}
        </button>
        ${copied !== null && html`<p>${outcome}</p>`}
      </div>
    </div>
  `;
}

// The snapshot, or null when there's none. api answers a call that failed in Python with
// {error}, and in --dev-ui the mock's own import can fail.
async function firstSnapshot() {
  try {
    const snapshot = await api.snapshot();
    if (!snapshot?.error) return snapshot;
    console.error("The snapshot call failed", snapshot.error);
  } catch (error) {
    console.error("Loading the snapshot failed", error);
  }
  return null;
}

// Applies the snapshot, and says whether that worked.
function showSnapshot(snapshot) {
  try {
    loadSnapshot(snapshot);
    return true;
  } catch (error) {
    console.error("Showing the snapshot failed", error);
    return false;
  }
}

async function start() {
  for (const [type, listener] of Object.entries(WINDOW_LISTENERS)) {
    window.addEventListener(type, listener);
  }
  // Subscribe before asking for the snapshot, so no event in between is missed.
  onEvent(applyEvent);
  const root = document.getElementById("app");
  render(html`<${App} />`, root);
  const snapshot = await firstSnapshot();
  if (snapshot && showSnapshot(snapshot)) {
    watchUpdateNotices(snapshot.components);
    return;
  }
  render(html`<${StartFailed} />`, root);
}

start();
