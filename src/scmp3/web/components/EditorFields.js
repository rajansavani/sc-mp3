// The editor's tag fields and the featured-artist style switch. Typing is sent as hand edits.
import { html } from "htm/preact";
import { useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError } from "../store.js";
import { strings } from "../strings/index.js";

const text = strings.main;

const NUMERIC_FIELDS = new Set(["year", "track_no"]);
const GRID_FIELDS = ["album", "year", "genre", "track_no"];
const MORE_FIELDS = ["album_artist"];
const PRESET_STYLES = ["artist_field", "feat_in_title", "all_in_title"];

// Fields whose tag value isn't a plain string. "3/12" holds the track number and the total.
const RENDERED = {
  year: (tags) => (tags.year == null ? "" : String(tags.year)),
  track_no: (tags) => {
    if (tags.track_no == null) return "";
    return tags.track_total ? `${tags.track_no}/${tags.track_total}` : String(tags.track_no);
  },
};

export function renderedValue(job, field) {
  if (!job.tags) return "";
  return RENDERED[field]?.(job.tags) ?? job.tags[field] ?? "";
}

export function fieldId(field) {
  return `field-${field}`;
}

function Field({ name, drafts, size = "normal" }) {
  const label = text.fields[name];
  const id = fieldId(name);
  const reason = drafts.reason(name);
  const resetText = size === "normal" ? text.reset : text.resetToSuggestion;
  // The grid's fields are narrower, so they keep the design's tighter padding.
  const inputClass =
    size === "normal" ? "text-field is-compact" : `text-field field-input is-${size}`;
  return html`
    <div class="field">
      <div class="field-label-row">
        <label id=${`${id}-label`} for=${id}>${label}</label>
        ${drafts.isOverridden(name) &&
        html`
          <button
            type="button"
            class="text-button"
            aria-describedby=${`${id}-label`}
            onClick=${() => drafts.reset(name)}
          >
            ${resetText}
          </button>
        `}
      </div>
      <input
        id=${id}
        class=${inputClass}
        type="text"
        inputmode=${NUMERIC_FIELDS.has(name) ? "numeric" : undefined}
        value=${drafts.value(name)}
        aria-invalid=${reason ? "true" : undefined}
        aria-describedby=${reason ? `${id}-error` : undefined}
        onInput=${(event) => drafts.type(name, event.currentTarget.value)}
        onFocus=${() => drafts.focus(name)}
        onBlur=${() => drafts.blur(name)}
      />
      ${reason &&
      html`
        <span id=${`${id}-error`} class="field-error">
          ${text.fieldErrors[reason] ?? text.fieldErrors.other}
        </span>
      `}
    </div>
  `;
}

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
      <legend>${text.styleLegend}</legend>
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
              ${text.styles[style]}
            </button>
          `,
        )}
      </div>
    </fieldset>
  `;
}

export function EditorFields({ job, settings, drafts }) {
  // A job with a hand edit in one of the extra fields opens with them showing.
  const [showMore, setMore] = useState(() => {
    return MORE_FIELDS.some((field) => drafts.isOverridden(field));
  });
  return html`
    <div class="editor-fields">
      <${Field} name="title" drafts=${drafts} size="title" />
      <${Field} name="artist" drafts=${drafts} size="artist" />
      <${StyleSwitch} job=${job} settings=${settings} />
      <div class="field-grid">
        ${GRID_FIELDS.map((name) => html`<${Field} key=${name} name=${name} drafts=${drafts} />`)}
      </div>
      <button
        type="button"
        class="text-button more-fields"
        aria-expanded=${String(showMore)}
        aria-controls="more-fields"
        onClick=${() => setMore(!showMore)}
      >
        ${showMore ? text.fewerFields : text.moreFields}
      </button>
      <div id="more-fields" class="field-grid" hidden=${!showMore}>
        ${MORE_FIELDS.map((name) => html`<${Field} key=${name} name=${name} drafts=${drafts} />`)}
      </div>
    </div>
  `;
}
