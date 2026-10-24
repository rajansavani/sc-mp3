// The Engine and updates section: the app's and the engine's versions and updates, YouTube
// support, the logs and diagnostics, the update checks, and the SoundCloud client ID override.
import { html } from "htm/preact";

import { api } from "../../api.js";
import { addToast, reportError } from "../../store.js";
import { strings } from "../../strings/index.js";
import { copyDiagnostics } from "../diagnostics.js";
import { youtubeProgressText } from "../YouTubeSupport.js";
import { errorText } from "../update-notices.js";
import { engineVersionLabel } from "../versions.js";
import { SelectSetting, Switches, TextSetting, bind } from "./fields.js";

const text = strings.settings.engine;

function Card({ title, detail, notes = [], children }) {
  return html`
    <div class="settings-card">
      <span class="settings-card-text">
        <span class="settings-card-title">${title}</span>
        ${detail && html`<span class="settings-card-detail">${detail}</span>`}
        ${notes.map((note) => html`<span class="settings-card-note">${note}</span>`)}
      </span>
      <span class="settings-card-actions">${children}</span>
    </div>
  `;
}

// A button that reads as busy, and does nothing, while its call runs.
function Action({ label, busyLabel = label, busy = false, onClick }) {
  return html`
    <button
      type="button"
      class="control-button is-ghost is-small"
      aria-disabled=${busy ? "true" : null}
      onClick=${busy ? null : onClick}
    >
      ${busy ? busyLabel : label}
    </button>
  `;
}

function appDetail(update) {
  if (update?.state === "available") {
    return html`${text.appAvailable(update.version)} <a href=${update.url}>${text.releaseNotes}</a>`;
  }
  return update?.state === "current" ? text.appCurrent : text.appUnknown;
}

// Check now and Update now return at once. update-notices.js says what they found.
const checkNow = async () => reportError(await api.check_updates());
const updateNow = async () => reportError(await api.update_engine());

function AppCard({ components, running }) {
  return html`
    <${Card}
      title=${text.app(components?.app_version ?? "")}
      detail=${appDetail(components?.app_update)}
    >
      <${Action}
        label=${text.checkNow}
        busyLabel=${text.checking}
        busy=${running === "check"}
        onClick=${running ? null : checkNow}
      />
    <//>
  `;
}

function engineNotes(engine) {
  const notes = [];
  const next = engine?.restart_to;
  if (next?.source === "updated") notes.push(text.restartToUpdate(engineVersionLabel(next.version)));
  if (next?.source === "built_in") notes.push(text.restartToBuiltIn(engineVersionLabel(next.version)));
  if (engine?.failed_version) notes.push(text.failed(engineVersionLabel(engine.failed_version)));
  return notes;
}

// Revert is offered while the next start would use a downloaded update, and after one failed,
// so its notice can be cleared.
function canRevert(engine) {
  const nextSource = engine?.restart_to?.source ?? engine?.source;
  return nextSource === "updated" || Boolean(engine?.failed_version);
}

// Where the engine in use came from. An update says its channel; the built-in copy has none.
function engineDetail(engine) {
  const source = text.sources[engine?.source] ?? text.sources.built_in;
  const channel = engine?.channel && text.channels[engine.channel];
  return channel ? `${source} · ${channel}` : source;
}

async function revert() {
  const result = await api.revert_engine();
  if (!reportError(result)) addToast("info", text.reverted);
}

function EngineCard({ components, updates }) {
  const engine = components?.engine;
  const version = engineVersionLabel(components?.engine_version);
  const running = updates?.running;
  return html`
    <${Card}
      title=${text.engine(version)}
      detail=${version ? engineDetail(engine) : text.versionUnknown}
      notes=${engineNotes(engine)}
    >
      <${Action}
        label=${text.updateNow}
        busyLabel=${text.updating(updates?.pct ?? null)}
        busy=${running === "update"}
        onClick=${running ? null : updateNow}
      />
      ${canRevert(engine) &&
      html`<${Action} label=${text.revert} busy=${Boolean(running)} onClick=${revert} />`}
    <//>
  `;
}

function youtubeDetail(youtube) {
  switch (youtube?.state) {
    case "installed":
      return text.youtubeInstalled(youtube.version);
    case "installing":
      return youtubeProgressText(youtube);
    case "failed":
      return `${text.youtubeFailed} ${errorText(youtube.error)}`;
    default:
      return text.youtubeMissing;
  }
}

function YoutubeCard({ youtube }) {
  const installing = youtube?.state === "installing";
  const canInstall = !installing && youtube?.state !== "installed";
  const install = async () => reportError(await api.install_youtube_support());
  const cancel = async () => reportError(await api.cancel_youtube_support());
  return html`
    <${Card} title=${text.youtube} detail=${youtubeDetail(youtube)}>
      ${canInstall && html`<${Action} label=${text.installNow} onClick=${install} />`}
      ${installing && html`<${Action} label=${strings.youtube.cancel} onClick=${cancel} />`}
    <//>
  `;
}

async function copyAndSay() {
  const copied = await copyDiagnostics();
  const shell = strings.shell;
  addToast(copied ? "info" : "error", copied ? shell.diagnosticsCopied : shell.diagnosticsNotCopied);
}

function LogsCard() {
  const openLogs = async () => reportError(await api.open_logs());
  return html`
    <${Card} title=${text.logs} detail=${text.logsDetail}>
      <${Action} label=${text.openLogs} onClick=${openLogs} />
      <${Action} label=${strings.shell.copyDiagnostics} onClick=${copyAndSay} />
    <//>
  `;
}

function Advanced({ settings, form }) {
  return html`
    <details class="settings-advanced">
      <summary>${text.advanced}</summary>
      <${TextSetting}
        ...${bind(form, "soundcloud_client_id")}
        shown=${settings.soundcloud_client_id ?? ""}
        toValue=${(typed) => typed.trim() || null}
        hint=${strings.settings.hints.soundcloud_client_id}
        placeholder=${text.clientIdPlaceholder}
        mono
      />
    </details>
  `;
}

export function EngineSection({ settings, form, components }) {
  const updates = components?.updates;
  return html`
    <${AppCard} components=${components} running=${updates?.running} />
    <${EngineCard} components=${components} updates=${updates} />
    <${SelectSetting} ...${bind(form, "engine_channel")} value=${settings.engine_channel} />
    <${YoutubeCard} youtube=${components?.youtube_support} />
    <${LogsCard} />
    <${Switches}
      names=${["check_app_updates", "check_engine_updates"]}
      settings=${settings}
      form=${form}
    />
    <${Advanced} settings=${settings} form=${form} />
  `;
}
