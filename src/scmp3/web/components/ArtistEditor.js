// Who's credited on a track: the main and featured rows, where featured artists go, and what
// saves. "Type the credit yourself" swaps the rows for a free-text artist, which wins.
import { html } from "htm/preact";
import { useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError, useStore } from "../store.js";
import { strings } from "../strings/index.js";
import { useArtistList } from "./artist-list.js";
import { ArtistRows } from "./ArtistRows.js";

const text = strings.artists;
const styleText = strings.main;

const PRESET_STYLES = ["artist_field", "feat_in_title", "all_in_title"];

function StyleSwitch({ job, settings }) {
  const setting = settings?.title_style ?? "artist_field";
  const current = job.style ?? setting;
  // Custom templates only show up as a choice when the setting uses them.
  const usesCustom = setting === "custom" || current === "custom";
  const choices = usesCustom ? [...PRESET_STYLES, "custom"] : PRESET_STYLES;
  const pick = (style) => {
    if (style !== current) api.set_style(job.id, style).then(reportError);
  };
  return html`
    <fieldset class="style-switch">
      <legend>${styleText.styleLegend}</legend>
      <div class="segmented">
        ${choices.map(
          (style) => html`
            <button
              key=${style}
              type="button"
              class="segment"
              aria-pressed=${String(style === current)}
              onClick=${() => pick(style)}
            >
              ${styleText.styles[style]}
            </button>
          `,
        )}
      </div>
    </fieldset>
  `;
}

// The artist folder from the planned file name, which Python works out as a save does. A
// re-tagged file stays in its own folder, so it has none.
function artistFolder(job, settings) {
  if (job.kind === "retag") return null;
  if (!settings?.artist_folders || !job.filename?.includes("/")) return null;
  return job.filename.split("/")[0];
}

// The title, artist and folder exactly as Python rendered them for the save.
function SavesAs({ job, settings }) {
  const tags = job.tags;
  if (!tags) return null;
  const folder = artistFolder(job, settings);
  return html`
    <div class="artist-saves">
      <span class="artist-saves-label">${text.savesAs}</span>
      <span class="artist-saves-lines" aria-live="polite">
        <span class="artist-saves-title">${text.savedTitle(tags.title)}</span>
        <span>${tags.artist ? text.savedArtist(tags.artist) : text.noArtist}</span>
        ${folder && html`<span>${text.savedFolder(folder)}</span>`}
      </span>
    </div>
  `;
}

// Today's free-text artist box. What's typed is a hand edit that wins over the list.
function TypedCredit({ drafts, idBase }) {
  const id = `${idBase}-artist`;
  return html`
    <div class="field">
      <div class="field-label-row">
        <label for=${id}>${text.typedLabel}</label>
      </div>
      <input
        id=${id}
        class="text-field field-input is-artist"
        type="text"
        value=${drafts.value("artist")}
        onInput=${(event) => drafts.type("artist", event.currentTarget.value)}
        onFocus=${() => drafts.focus("artist")}
        onBlur=${() => drafts.blur("artist")}
      />
    </div>
  `;
}

// `idBase` keeps the ids apart when the editor also opens in a set's track table.
export function ArtistEditor({ job, drafts, idBase = "artists" }) {
  const { settings } = useStore();
  const artists = useArtistList(job, drafts);
  const [typing, setTyping] = useState(false);
  const typed = typing || drafts.isOverridden("artist");
  const toggleTyping = () => {
    if (!typed) {
      setTyping(true);
      // The box takes focus once it's there.
      requestAnimationFrame(() => document.getElementById(`${idBase}-artist`)?.focus());
      return;
    }
    // Back to the list, which drops the typed text.
    setTyping(false);
    drafts.reset("artist");
  };
  const reset =
    artists.edited &&
    html`
      <button
        type="button"
        class="text-button"
        aria-label=${text.resetLabel}
        onClick=${artists.reset}
      >
        ${text.reset}
      </button>
    `;
  return html`
    <div class="artist-editor">
      ${typed
        ? html`<${TypedCredit} drafts=${drafts} idBase=${idBase} />`
        : html`
            <${ArtistRows}
              list=${artists.list}
              onChange=${artists.change}
              idBase=${idBase}
              reset=${reset}
            />
          `}
      ${job.needs_main_artist && html`<p class="field-error">${text.needsMainArtist}</p>`}
      <${StyleSwitch} job=${job} settings=${settings} />
      <${SavesAs} job=${job} settings=${settings} />
      <button type="button" class="text-button artist-mode" onClick=${toggleTyping}>
        ${typed ? text.useList : text.typeCredit}
      </button>
    </div>
  `;
}
