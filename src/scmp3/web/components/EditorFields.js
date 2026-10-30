// The editor's tag fields, with the artist editor under the title. Typing is sent as hand edits.
import { html } from "htm/preact";
import { useState } from "preact/hooks";

import { strings } from "../strings/index.js";
import { ArtistEditor } from "./ArtistEditor.js";

const text = strings.main;

const NUMERIC_FIELDS = new Set(["year", "track_no"]);
const GRID_FIELDS = ["album", "year", "genre", "track_no"];
const MORE_FIELDS = ["album_artist"];

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

// The states in which the file still holds the title it was read with. Once saving starts, the
// file gets the title above, so the hint goes.
const FILE_UNCHANGED = new Set(["needs_review", "error"]);

// A re-tag's title in the file now, under the new one while they differ.
function fileTitleHint(job, drafts) {
  if (!FILE_UNCHANGED.has(job.state)) return null;
  const now = job.kind === "retag" ? job.file_tags?.title : null;
  if (now == null || drafts.value("title") === now) return null;
  return now ? text.inFileNow(now) : text.inFileNowEmpty;
}

export function Field({ name, drafts, size = "normal", hint = null }) {
  const label = text.fields[name];
  const id = fieldId(name);
  const reason = drafts.reason(name);
  const described = [hint && `${id}-hint`, reason && `${id}-error`].filter(Boolean).join(" ");
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
        aria-describedby=${described || undefined}
        onInput=${(event) => drafts.type(name, event.currentTarget.value)}
        onFocus=${() => drafts.focus(name)}
        onBlur=${() => drafts.blur(name)}
      />
      ${hint && html`<span id=${`${id}-hint`} class="field-hint">${hint}</span>`}
      ${reason &&
      html`
        <span id=${`${id}-error`} class="field-error">
          ${text.fieldErrors[reason] ?? text.fieldErrors.other}
        </span>
      `}
    </div>
  `;
}

export function EditorFields({ job, drafts }) {
  // A job with a hand edit in one of the extra fields opens with them showing.
  const [showMore, setMore] = useState(() => {
    return MORE_FIELDS.some((field) => drafts.isOverridden(field));
  });
  return html`
    <div class="editor-fields">
      <${Field} name="title" drafts=${drafts} size="title" hint=${fileTitleHint(job, drafts)} />
      <${ArtistEditor} job=${job} drafts=${drafts} />
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
